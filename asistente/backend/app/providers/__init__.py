"""Provider registry: config in, one ``LlmProvider`` out."""

from __future__ import annotations

from app.config import (
    PROVIDER_AZURE,
    PROVIDER_FAKE,
    PROVIDER_GEMINI,
    PROVIDER_VERTEX,
    Settings,
)
from app.providers.azure_openai import AzureOpenAIProvider
from app.providers.base import LlmProvider, ProviderError
from app.providers.fake import FakeProvider
from app.providers.gemini import GeminiProvider
from app.providers.vertex_ai import VertexAIProvider

__all__ = [
    "AzureOpenAIProvider",
    "FakeProvider",
    "GeminiProvider",
    "LlmProvider",
    "ProviderError",
    "VertexAIProvider",
    "build_provider",
]


def build_provider(settings: Settings) -> LlmProvider:
    """Instantiate the provider named by ``LLM_PROVIDER``."""
    if settings.provider == PROVIDER_AZURE:
        return AzureOpenAIProvider(settings.azure, timeout=settings.request_timeout)
    if settings.provider == PROVIDER_GEMINI:
        return GeminiProvider(settings.gemini, timeout=settings.request_timeout)
    if settings.provider == PROVIDER_VERTEX:
        return VertexAIProvider(settings.vertex, timeout=settings.request_timeout)
    if settings.provider == PROVIDER_FAKE:
        return FakeProvider()
    raise ProviderError(f"unknown provider: {settings.provider}")
