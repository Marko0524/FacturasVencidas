"""The seam between the assistant and whoever runs the model.

Two methods, because the assistant needs exactly two things from a provider:
turn text into vectors so it can retrieve, and turn a prompt into an answer.
Everything above this file — intent routing, permission scoping, grounding,
escalation — is provider-agnostic, which is what makes swapping Azure OpenAI
for Gemini a configuration change instead of a rewrite.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class ProviderError(Exception):
    """The model could not be reached, or answered with something unusable.

    Callers treat this as one more negative branch: escalate to a human. A
    provider outage must never surface as an invented answer.
    """


@runtime_checkable
class LlmProvider(Protocol):
    """What the assistant requires of a model provider."""

    name: str

    def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
        """Answer a single prompt. Deterministic settings are the provider's
        job: this assistant never wants creative variation."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order."""
