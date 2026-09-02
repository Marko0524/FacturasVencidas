"""Google Gemini provider (AI Studio REST API).

The same two operations as Azure, shaped differently: the model id lives in the
URL path, the system prompt travels in its own field, and embeddings are
requested one text at a time through ``batchEmbedContents``.
"""

from __future__ import annotations

import logging

import requests

from app.config import GeminiSettings
from app.providers.base import ProviderError, presupuesto_de_razonamiento
from app.providers.http import post_json

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    """Chat and embeddings against Google AI Studio."""

    name = "gemini"

    def __init__(self, settings: GeminiSettings, timeout: float = 30.0) -> None:
        if not settings.configured:
            raise ProviderError("Gemini is not configured: set GEMINI_API_KEY")
        self._settings = settings
        self._timeout = timeout
        self._session = requests.Session()

    def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
        payload = {
            # Gemini keeps the system prompt out of the conversation turns,
            # which is exactly what we want: instructions the retrieved
            # documents cannot pose as.
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingBudget": presupuesto_de_razonamiento(max_tokens)},
            },
        }
        data = self._post(f"models/{self._settings.chat_model}:generateContent", payload)
        return self._first_text(data)

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._settings.embedding_model
        payload = {
            "requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": text}]}}
                for text in texts
            ]
        }
        data = self._post(f"models/{model}:batchEmbedContents", payload)
        try:
            return [item["values"] for item in data["embeddings"]]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected Gemini embeddings payload: {data}") from exc

    @staticmethod
    def _first_text(data: dict) -> str:
        """Pull the answer out, and turn a silent block into a loud failure.

        A response filtered by safety settings or cut by the token limit comes
        back with no ``parts`` at all. Returning "" there would look like a
        model that had nothing to say; raising sends it down the escalation
        path, where it belongs.
        """
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "sin candidatos")
            raise ProviderError(f"Gemini returned no answer (reason={reason})")

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text:
            raise ProviderError(
                f"Gemini returned an empty answer (finishReason={candidate.get('finishReason')})"
            )
        # Una respuesta a medias es peor que ninguna: el JSON cortado llega
        # arriba como "el modelo no devuelve JSON válido", que manda a leer el
        # prompt cuando lo que faltaron fueron tokens. Se dice aquí, donde se
        # sabe por qué.
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise ProviderError(
                "Gemini truncated the answer (finishReason=MAX_TOKENS): "
                "raise max_tokens or lower the thinking budget"
            )
        return text

    def _post(self, path: str, payload: dict) -> dict:
        return post_json(
            self._session,
            f"{API_ROOT}/{path}",
            # The key goes in a header, not the query string: query strings end
            # up in proxy and server logs.
            headers={
                "x-goog-api-key": self._settings.api_key,
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=self._timeout,
            provider="Gemini",
        )
