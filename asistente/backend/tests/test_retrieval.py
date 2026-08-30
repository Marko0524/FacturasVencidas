"""Retrieval tests. The permission ones are the reason this file exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.retrieval import SCOPE_CUSTOMER, SCOPE_PUBLIC, Chunk, load_corpus, parse_document
from tests.conftest import AURORA, LOGISTICA, MERIDIANO

DOCUMENT = """---
titulo: Documento de prueba
alcance: cliente
cliente: Alguien@Ejemplo.MX
---

Introducción sin encabezado.

## Primera sección

Contenido de la primera.

## Segunda sección

Contenido de la segunda.
"""


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- parsing -----------------------------------------------------------------


def test_front_matter_and_sections_become_chunks(tmp_path: Path):
    chunks = parse_document(write(tmp_path, "doc.md", DOCUMENT))

    assert [c.id for c in chunks] == ["doc#0", "doc#1", "doc#2"]
    assert all(c.title == "Documento de prueba" for c in chunks)
    assert all(c.scope == SCOPE_CUSTOMER for c in chunks)
    assert "Primera sección" in chunks[1].text


def test_a_customer_document_without_an_owner_is_rejected(tmp_path: Path):
    """A fragment that lost track of who owns it could not be authorised."""
    broken = "---\ntitulo: X\nalcance: cliente\n---\n\n## A\n\ntexto\n"

    with pytest.raises(ValueError, match="alcance=cliente"):
        parse_document(write(tmp_path, "roto.md", broken))


def test_documents_without_front_matter_default_to_public(tmp_path: Path):
    chunks = parse_document(write(tmp_path, "simple.md", "## Uno\n\ntexto\n"))

    assert chunks[0].scope == SCOPE_PUBLIC


# --- permissions -------------------------------------------------------------


def test_public_chunks_are_visible_to_everyone():
    chunk = Chunk("a#0", "a.md", "T", SCOPE_PUBLIC, "", "texto")

    assert chunk.visible_to(LOGISTICA)
    assert chunk.visible_to(AURORA)


def test_customer_chunks_are_visible_only_to_their_owner():
    chunk = Chunk("a#0", "a.md", "T", SCOPE_CUSTOMER, LOGISTICA, "texto")

    assert chunk.visible_to(LOGISTICA)
    assert not chunk.visible_to(MERIDIANO)


def test_owner_matching_ignores_case(tmp_path: Path):
    chunk = parse_document(write(tmp_path, "doc.md", DOCUMENT))[0]

    assert chunk.visible_to("alguien@ejemplo.mx")


def test_an_anonymous_caller_sees_no_customer_document():
    chunk = Chunk("a#0", "a.md", "T", SCOPE_CUSTOMER, LOGISTICA, "texto")

    assert not chunk.visible_to("")


# --- search ------------------------------------------------------------------


def test_search_never_returns_another_customers_document(retriever):
    """The heart of the design: not filtered out afterwards — never a candidate."""
    hits = retriever.search("deducible en transporte de mercancías", MERIDIANO)

    documents = {hit.chunk.document for hit in hits}
    assert "anexo-logistica-pacifico.md" not in documents


def test_the_owner_does_reach_their_own_annex(retriever):
    """The counterpart: scoping must not make the document unreachable."""
    hits = retriever.search("deducible preferente en transporte de mercancías", LOGISTICA)

    assert "anexo-logistica-pacifico.md" in {hit.chunk.document for hit in hits}


def test_an_unrelated_question_returns_nothing(retriever):
    """Without a floor the retriever always hands back its best guess."""
    hits = retriever.search("cuál es la capital de Mongolia", LOGISTICA)

    assert hits == []


def test_results_are_capped_at_top_k(retriever):
    retriever._top_k = 2  # noqa: SLF001 - test seam

    assert len(retriever.search("pago de facturas y pólizas", LOGISTICA)) <= 2


def test_results_come_back_ordered_by_similarity(retriever):
    hits = retriever.search("periodo de gracia y cancelación", LOGISTICA)

    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


def test_the_shipped_corpus_parses(settings):
    chunks = load_corpus(settings.corpus_path)

    assert len(chunks) > 5
    assert {c.scope for c in chunks} == {SCOPE_PUBLIC, SCOPE_CUSTOMER}
