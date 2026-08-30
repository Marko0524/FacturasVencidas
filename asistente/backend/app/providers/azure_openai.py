"""Azure OpenAI provider.

Plain REST over ``requests``, no SDK: two endpoints and a header is the whole
contract, and the dependency list stays at what the repository already installs.
"""

from __future__ import annotations

import logging

import requests

from app.config import AzureSettings
from app.providers.base import ProviderError
from app.providers.http import post_json

logger = logging.getLogger(__name__)


class AzureOpenAIProvider:
    """Chat and embeddings against an Azure OpenAI resource.

    Azure addresses a *deployment*, not a model: the same account can expose
    ``gpt-4o`` under any name its owner chose. That is why the deployment names
    are configuration and never appear hardcoded here.
    """

    name = "azure"

    def __init__(self, settings: AzureSettings, timeout: float = 30.0) -> None:
        if not settings.configured:
            raise ProviderError(
                "Azure OpenAI is not configured: set AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_CHAT_DEPLOYMENT"
            )
        self._settings = settings
        self._timeout = timeout
        self._session = requests.Session()

    def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        data = self._post("chat/completions", self._settings.chat_deployment, payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected Azure OpenAI chat payload: {data}") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._settings.embedding_deployment:
            raise ProviderError("AZURE_OPENAI_EMBEDDING_DEPLOYMENT is not set")
        data = self._post("embeddings", self._settings.embedding_deployment, {"input": texts})
        try:
            # The API is documented to preserve order, but it also returns an
            # explicit index. Sorting by it costs nothing and removes the doubt.
            items = sorted(data["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected Azure OpenAI embeddings payload: {data}") from exc

    def _post(self, operation: str, deployment: str, payload: dict) -> dict:
        url = (
            f"{self._settings.endpoint}/openai/deployments/{deployment}/{operation}"
            f"?api-version={self._settings.api_version}"
        )
        # The error body carries the useful part (quota, content filter, a
        # deployment name that does not exist); the status code alone explains
        # nothing, so ``post_json`` keeps it in the message.
        return post_json(
            self._session,
            url,
            headers={"api-key": self._settings.api_key, "Content-Type": "application/json"},
            payload=payload,
            timeout=self._timeout,
            provider="Azure OpenAI",
        )
