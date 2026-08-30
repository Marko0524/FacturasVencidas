"""Guardrails: grounding, prompt hygiene and input limits.

The strongest guardrail in this assistant is not in this file — it is the fact
that invoice figures are inserted by code and that unauthorised documents never
reach the prompt. What is left here handles the documental path, where the model
does write prose, and the prompt boundary, where retrieved text meets
instructions.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

NOT_FOUND = "NO_ENCONTRADO"

# Phrases that only make sense if someone is talking *to the model* through a
# document or a question. Matching them is not a security boundary — a
# determined rewrite gets past any list — it is a tripwire that turns a likely
# attack into an escalation instead of an answer.
INJECTION_PATTERNS = (
    r"ignora (todas )?(las )?instrucciones",
    r"olvida (todo|las instrucciones)",
    r"ignore (all )?(previous )?instructions",
    r"disregard (the )?(above|previous)",
    r"eres ahora\b",
    r"you are now\b",
    r"act[úu]a como si",
    r"revela (el|tu) (prompt|sistema)",
    r"reveal (the|your) (system )?prompt",
    # "muéstrame" and "muestrame" are the same request; the accent must not be
    # the difference between a tripwire and a bypass.
    r"mu[eé]stra(me)?\s+(el|tu)\s+prompt",
    r"prompt del sistema",
    r"system prompt",
)
INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


class GroundingError(Exception):
    """The answer could not be tied to the evidence that was retrieved."""


class SinEvidencia(GroundingError):
    """El modelo dijo, correctamente, que la respuesta no está en lo recuperado.

    Se separa del resto de errores de anclaje porque no es lo mismo. Un JSON
    roto o una cita inventada son un modelo portándose mal, y eso merece que lo
    vea una persona. Esto es un modelo portándose *bien*: se le pidió que no
    respondiera sin respaldo y no respondió. Lo que falta no es criterio humano,
    es un dato —otra palabra, otro documento— que tiene delante quien pregunta.
    """


def looks_like_injection(text: str) -> bool:
    """True when the text tries to address the model rather than ask it."""
    return bool(INJECTION.search(text))


def sanitize_question(question: str, max_chars: int) -> str:
    """Trim and bound the user's question.

    The length cap is not cosmetic: an unbounded question is an unbounded bill
    and a way to push the real instructions out of the context window.
    """
    cleaned = question.strip()
    if not cleaned:
        raise ValueError("la pregunta está vacía")
    return cleaned[:max_chars]


def parse_grounded_answer(raw: str, allowed_ids: set[str]) -> tuple[str, list[str]]:
    """Validate the model's JSON answer against the evidence it was given.

    The model is required to cite the fragments it used. Two things are checked,
    and both are refusals rather than corrections:

    * the citation list is not empty — an answer from nowhere is not grounded;
    * every cited id was actually retrieved — a plausible-looking id the model
      composed itself is the exact signature of a fabricated answer.

    Citing is not proof that the sentence follows from the fragment. It is a
    cheap, checkable claim that raises the cost of inventing one, and it fails
    closed.
    """
    payload = _extract_json(raw)
    if payload is None:
        raise GroundingError("la respuesta del modelo no es JSON válido")

    answer = str(payload.get("respuesta", "")).strip()
    cited = payload.get("fragmentos") or []
    if not isinstance(cited, list):
        raise GroundingError("'fragmentos' no es una lista")
    cited = [str(item).strip() for item in cited if str(item).strip()]

    if not answer:
        raise GroundingError("la respuesta viene vacía")
    if answer == NOT_FOUND:
        raise SinEvidencia("el modelo no encontró la respuesta en la documentación")
    if not cited:
        raise GroundingError("la respuesta no cita ningún fragmento")

    invented = [item for item in cited if item not in allowed_ids]
    if invented:
        raise GroundingError(f"cita fragmentos que no se recuperaron: {', '.join(invented)}")

    return answer, cited


def _extract_json(raw: str) -> dict | None:
    """Parse the JSON object, tolerating a fenced code block around it.

    Models wrap JSON in ```json fences often enough that refusing it would
    escalate on formatting rather than on substance.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            return None

    return payload if isinstance(payload, dict) else None
