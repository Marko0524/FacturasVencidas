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


# Los modelos razonadores de la familia Gemini 2.5 cobran lo que piensan contra
# `maxOutputTokens`: el razonamiento y la respuesta salen de la misma bolsa. Sin
# un tope, pensar se come el presupuesto y la respuesta vuelve cortada a media
# frase — que es peor que un fallo, porque un JSON truncado se lee como un modelo
# que se porta mal en vez de como un presupuesto que no alcanzó.
#
# Se reserva un cuarto para pensar y nunca más de este tope. La fracción importa
# porque las llamadas no piden lo mismo: al clasificador le bastan 512 en total,
# y un presupuesto fijo de razonamiento se los comería enteros.
MAX_THINKING_TOKENS = 512


def presupuesto_de_razonamiento(max_tokens: int) -> int:
    """Cuánto puede pensar el modelo sin dejar a la respuesta sin sitio."""
    return min(MAX_THINKING_TOKENS, max(0, max_tokens // 4))


@runtime_checkable
class LlmProvider(Protocol):
    """What the assistant requires of a model provider."""

    name: str

    def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
        """Answer a single prompt. Deterministic settings are the provider's
        job: this assistant never wants creative variation."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order."""
