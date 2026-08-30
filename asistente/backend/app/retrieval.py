"""Document retrieval, permission-scoped.

The design decision this file exists to enforce: **an unauthorised fragment is
not a rejected candidate, it is not a candidate at all.** The scope filter runs
before scoring, so a document belonging to another customer never reaches the
ranking, never reaches the prompt, and cannot leak through a model that was
talked into ignoring its instructions.

Doing it the other way round — retrieve globally, then ask the model to only
use what the user may see — makes the permission boundary a matter of the
model's obedience. It is not a boundary at that point; it is a request.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from app.providers.base import LlmProvider

logger = logging.getLogger(__name__)

SCOPE_PUBLIC = "publico"
SCOPE_CUSTOMER = "cliente"

# Roughly a section each. Splitting on markdown headings keeps a chunk about one
# subject, which is what makes a citation meaningful to whoever reads it.
HEADING = re.compile(r"^##\s+", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    """A retrievable fragment and everything needed to authorise and cite it."""

    id: str
    document: str
    title: str
    scope: str
    customer: str
    text: str

    def visible_to(self, customer_email: str) -> bool:
        """Public fragments are for everyone; customer fragments for one."""
        if self.scope == SCOPE_PUBLIC:
            return True
        return bool(customer_email) and self.customer.lower() == customer_email.lower()


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float


def parse_document(path: Path) -> list[Chunk]:
    """Split one markdown file into chunks, carrying its front matter down.

    The scope is a property of the *document*, so every chunk inherits it. A
    fragment that lost track of who owns it would be unauthorisable.
    """
    raw = path.read_text(encoding="utf-8")
    metadata, body = _split_front_matter(raw)

    scope = metadata.get("alcance", SCOPE_PUBLIC).strip().lower()
    customer = metadata.get("cliente", "").strip()
    title = metadata.get("titulo", path.stem)

    if scope == SCOPE_CUSTOMER and not customer:
        raise ValueError(f"{path.name}: alcance=cliente requires a 'cliente' field")

    chunks = []
    for index, section in enumerate(_split_sections(body)):
        text = section.strip()
        if not text:
            continue
        chunks.append(
            Chunk(
                id=f"{path.stem}#{index}",
                document=path.name,
                title=title,
                scope=scope,
                customer=customer,
                text=text,
            )
        )
    return chunks


def load_corpus(corpus_path: Path) -> list[Chunk]:
    """Read every markdown document in the corpus directory."""
    if not corpus_path.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {corpus_path}")

    chunks: list[Chunk] = []
    for path in sorted(corpus_path.glob("*.md")):
        chunks.extend(parse_document(path))
    logger.info("Corpus loaded documents=%d chunks=%d", len(list(corpus_path.glob("*.md"))), len(chunks))
    return chunks


class Retriever:
    """In-memory vector search over the corpus.

    In-memory is a deliberate scope choice, not an oversight: a few dozen
    fragments do not need Azure AI Search, and the interesting part of the
    design — the scope filter — is identical either way. What would change with
    a real index is where the filter is expressed, not that it comes first.
    """

    def __init__(self, chunks: list[Chunk], provider: LlmProvider, *, top_k: int = 4,
                 min_similarity: float = 0.55) -> None:
        self._chunks = chunks
        self._provider = provider
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._vectors: list[list[float]] | None = None

    def index(self) -> None:
        """Embed the corpus once. Called on startup, not per request."""
        if not self._chunks:
            self._vectors = []
            return
        self._vectors = self._provider.embed([chunk.text for chunk in self._chunks])
        logger.info("Corpus indexed chunks=%d provider=%s", len(self._chunks), self._provider.name)

    def search(self, question: str, customer_email: str) -> list[ScoredChunk]:
        """Return the best fragments **this customer is allowed to see**."""
        if self._vectors is None:
            self.index()

        visible = [
            (chunk, vector)
            for chunk, vector in zip(self._chunks, self._vectors or [])
            if chunk.visible_to(customer_email)
        ]
        if not visible:
            return []

        query = self._provider.embed([question])[0]
        scored = [
            ScoredChunk(chunk, _cosine(query, vector))
            for chunk, vector in visible
        ]
        scored.sort(key=lambda item: item.score, reverse=True)

        # The floor is what lets the assistant say "I don't know". Without it
        # the top result is always returned, however unrelated.
        return [item for item in scored[: self._top_k] if item.score >= self._min_similarity]


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """Parse the leading ``---`` block. Flat ``key: value`` pairs only."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw

    metadata: dict[str, str] = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip()
    return metadata, parts[2]


def _split_sections(body: str) -> list[str]:
    """Split on level-two headings, keeping the heading with its section."""
    pieces = HEADING.split(body)
    if len(pieces) == 1:
        return [body]
    # ``pieces[0]`` is whatever preceded the first heading; the rest lost their
    # "## " marker to the split, so it is put back.
    return [pieces[0]] + [f"## {piece}" for piece in pieces[1:]]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)
