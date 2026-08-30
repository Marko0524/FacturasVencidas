"""A provider that needs no network, no key and no budget.

It exists so the whole assistant — routing, permission scoping, grounding,
escalation — can be tested deterministically, and so ``LLM_PROVIDER=fake`` runs
the app end to end on a fresh clone. It is a test double, not a model: the
embeddings only count shared words and the answers are canned.
"""

from __future__ import annotations

import json
import math
import re

VECTOR_SIZE = 4096
WORD = re.compile(r"[a-záéíóúñü0-9]+", re.IGNORECASE)

# Function words carry no topic. Left in, they dominate the vector — every
# Spanish sentence shares them — and unrelated texts end up looking similar,
# which is precisely what the retrieval floor is supposed to catch.
STOPWORDS = frozenset("""
a al algo alguna algunas alguno algunos ante antes cada casi como con contra cual
cuales cuando de del desde donde dos e el ella ellas ello ellos en entre era eran
es esa esas ese eso esos esta estan estar estas este esto estos fue fueron ha
hasta hay la las le les lo los mas me mi mis mucho muy no nos o os otra otras otro
otros para pero poco por porque que quien quienes se sea ser si sin sobre solo son
su sus tambien tan tanto te tiene tienen todo todos tu tus un una uno unos y ya
cual cuanto cuantos cuanta cuantas
""".split())


class FakeProvider:
    """Deterministic stand-in for a real model."""

    name = "fake"

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        # Keyed by a substring of the user prompt, so a test can steer one
        # specific call without stubbing the whole class.
        self._answers = answers or {}
        # Vocabulary assigned on first sight, one word per dimension.
        # Hashing words into buckets was the obvious approach and it was wrong:
        # with a three-word question, a single collision between an unrelated
        # word and a corpus word dominates the cosine, and "capital de
        # Mongolia" scored 0.25 against a billing policy. A real embedding
        # model has no such accident, so neither should its stand-in.
        self._vocabulary: dict[str, int] = {}

    def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
        for needle, answer in self._answers.items():
            if needle in user:
                return answer

        if "CLASIFICA" in system:
            return self._classify(user)
        return self._answer_from_context(user)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    # --- canned behaviours ---------------------------------------------------

    @staticmethod
    def _classify(user: str) -> str:
        """Keyword routing, which is all a stand-in owes the intent step."""
        lowered = user.lower()
        facturacion = ("factura", "pago", "pagar", "debo", "adeudo", "saldo", "cobro")
        if re.search(r"\binv-\d+\b", lowered) or any(w in lowered for w in facturacion):
            return "FACTURA"
        poliza = ("póliza", "poliza", "cobertura", "deducible", "vigencia", "gracia",
                  "cancelaci", "renovaci", "siniestro procedente")
        if any(word in lowered for word in poliza):
            return "POLIZA"
        return "HUMANO"

    @staticmethod
    def _answer_from_context(user: str) -> str:
        """Echo the first retrieved fragment and cite it.

        Citing a real id matters: the grounding check verifies the citation
        against what retrieval actually returned, so a double that invented ids
        would make every test pass through the escalation path instead.
        """
        ids = re.findall(r"\[([\w.-]+#\d+)\]", user)
        if not ids:
            return json.dumps({"respuesta": "NO_ENCONTRADO", "fragmentos": []}, ensure_ascii=False)
        return json.dumps(
            {"respuesta": f"Según la documentación consultada ({ids[0]}).", "fragmentos": [ids[0]]},
            ensure_ascii=False,
        )

    def _vector(self, text: str) -> list[float]:
        """Collision-free bag of content words, L2-normalised.

        One dimension per distinct word, assigned on first sight. Shared
        vocabulary pushes the cosine up; text with nothing in common lands at
        exactly zero, which is the property the retrieval floor depends on.
        Stopwords are dropped, because otherwise every Spanish sentence looks
        like every other one.
        """
        vector = [0.0] * VECTOR_SIZE
        for word in WORD.findall(text.lower()):
            if len(word) < 3 or word in STOPWORDS:
                continue
            index = self._vocabulary.setdefault(word, len(self._vocabulary))
            if index >= VECTOR_SIZE:
                # Only reachable with a corpus far larger than a stand-in is
                # meant to carry; wrapping keeps it working, degraded.
                index %= VECTOR_SIZE
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]
