"""A qué documento se refiere un resumen cuando no se dice cuál.

Esta rama no tenía pruebas: el recuperador en memoria que usan los demás tests
no sabe resumir, así que escalaba antes de llegar aquí y todo pasaba en verde
sin ejercitar nada. Se monta un recuperador de mentira con lo justo —listar y
devolver fragmentos— para poder ejercitarla.

Lo que se fija: sin referente se PREGUNTA, no se escala ni se adivina. Un
traspaso a un humano para averiguar cuál de los seis documentos del propio
cliente es, gasta una persona en algo que se resuelve con un toque; y adivinar
resume el documento equivocado con total aplomo, que es peor.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.assistant import Assistant, INTENT_SUMMARY
from app.invoices import InvoiceStore
from app.providers.fake import FakeProvider
from tests.conftest import LOGISTICA


@dataclass(frozen=True)
class Doc:
    nombre: str
    titulo: str


@dataclass(frozen=True)
class Trozo:
    id: str
    title: str
    document: str
    text: str
    scope: str = "cliente"


CORPUS = [
    Doc("caratula.md", "Carátula de póliza"),
    Doc("anexo.md", "Anexo de condiciones particulares"),
    Doc("convenio-flotilla.pdf", "Convenio de flotilla vehicular"),
    Doc("convenio-viajes.pdf", "Convenio de asistencia en viajes"),
]


class RecuperadorFalso:
    """Lo mínimo que `_answer_summary` necesita para funcionar."""

    def __init__(self, documentos=CORPUS) -> None:
        self._documentos = documentos

    def list_documents(self, cliente: str) -> list[Doc]:
        return list(self._documentos)

    def document_fragments(self, nombre: str, cliente: str) -> list[Trozo]:
        doc = next((d for d in self._documentos if d.nombre == nombre), None)
        if doc is None:
            return []
        return [Trozo(f"{nombre}#1", doc.titulo, nombre, f"Contenido de {doc.titulo}.")]

    def search(self, pregunta: str, cliente: str):
        return []


@pytest.fixture
def resumidor(settings, provider: FakeProvider):
    def construir(documentos=CORPUS) -> Assistant:
        return Assistant(
            settings=settings,
            provider=provider,
            retriever=RecuperadorFalso(documentos),
            invoice_store=InvoiceStore(settings.invoices_path),
        )

    return construir


def preguntar(asistente: Assistant, pregunta: str, documento: str = ""):
    return asistente._answer_summary(pregunta, LOGISTICA, documento)  # noqa: SLF001


# --- sin referente se pregunta ------------------------------------------------


def test_with_nothing_to_go_on_it_asks_instead_of_escalating(resumidor):
    """El caso que fallaba en cuanto no había nada seleccionado."""
    resultado = preguntar(resumidor(), "¿De qué trata?")

    assert not resultado.escalated
    assert resultado.intent == INTENT_SUMMARY
    assert resultado.data["tipo"] == "elegir_documento"


def test_the_question_offers_the_documents_by_name(resumidor):
    resultado = preguntar(resumidor(), "Dame un resumen")

    titulos = [o["titulo"] for o in resultado.data["opciones"]]
    assert titulos == [d.titulo for d in CORPUS]


def test_each_option_carries_the_name_needed_to_ask_again(resumidor):
    """El título es para leerlo; lo que vuelve al servidor es el nombre."""
    resultado = preguntar(resumidor(), "Resúmelo")

    assert all(o["nombre"] and o["titulo"] for o in resultado.data["opciones"])


def test_an_ambiguous_description_offers_only_what_matched(resumidor):
    """Ofrecer los cuatro cuando dos encajan es peor que la propia pregunta."""
    resultado = preguntar(resumidor(), "Resume el convenio")

    titulos = [o["titulo"] for o in resultado.data["opciones"]]
    assert len(titulos) == 2
    assert all("Convenio" in t for t in titulos)


def test_with_no_documents_at_all_it_does_escalate(resumidor):
    """Preguntar "¿cuál?" sin nada que ofrecer no lleva a ninguna parte."""
    resultado = preguntar(resumidor([]), "¿De qué trata?")

    assert resultado.escalated


# --- lo explícito sigue mandando ----------------------------------------------


def test_naming_the_document_still_wins(resumidor):
    resultado = preguntar(resumidor(), "Resume la carátula")

    assert resultado.data.get("tipo") != "elegir_documento"
    assert resultado.data["documento"] == "caratula.md"


def test_a_selected_document_is_used_when_the_question_names_none(resumidor):
    resultado = preguntar(resumidor(), "¿De qué trata?", documento="anexo.md")

    assert resultado.data["documento"] == "anexo.md"


def test_what_the_question_names_beats_what_was_selected(resumidor):
    """Lo que se acaba de decir manda sobre lo que quedó marcado antes."""
    resultado = preguntar(resumidor(), "Resume la carátula", documento="anexo.md")

    assert resultado.data["documento"] == "caratula.md"


def test_only_the_customers_own_documents_can_be_offered(resumidor):
    """La lista llega ya filtrada por permisos, así que la pregunta no puede
    revelar la existencia de un documento ajeno."""
    resultado = preguntar(resumidor([Doc("mio.md", "Sólo mío")]), "Resume")

    assert [o["nombre"] for o in resultado.data["opciones"]] == ["mio.md"]
