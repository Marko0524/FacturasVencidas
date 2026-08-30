"""Qué documento nombra una pregunta.

La función es pura: no toca base de datos ni modelo. Lo que se fija aquí es el
criterio — cuándo hay señal suficiente para elegir, y cuándo hay que preguntar
en vez de acertar por sorteo.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.assistant import resolver_documento


@dataclass(frozen=True)
class Doc:
    nombre: str
    titulo: str


CORPUS = [
    Doc("caratula-logistica-pacifico.md", "Carátula de póliza — Logistica Pacifico"),
    Doc("anexo-logistica-pacifico.md", "Anexo de condiciones particulares — Logistica Pacifico"),
    Doc("condiciones-generales.md", "Condiciones generales de las pólizas"),
    Doc("facturacion-y-cobranza.md", "Política de facturación y cobranza"),
    Doc("siniestros-y-reclamaciones.md", "Guía de siniestros y reclamaciones"),
    Doc("carga/x/convenio-flotilla.pdf", "Convenio de flotilla vehicular 2026"),
    Doc("carga/x/convenio-viajes.pdf", "Convenio de asistencia en viajes 2026"),
]


def elegido(pregunta: str) -> str:
    return resolver_documento(pregunta, CORPUS)[0]


def candidatos(pregunta: str) -> list[str]:
    return resolver_documento(pregunta, CORPUS)[1]


# --- señala uno ---------------------------------------------------------------


@pytest.mark.parametrize(
    "pregunta,esperado",
    [
        ("Resume la carátula de mi póliza", "caratula-logistica-pacifico.md"),
        ("Resume la carátula", "caratula-logistica-pacifico.md"),
        ("Dame un resumen de la guía de siniestros", "siniestros-y-reclamaciones.md"),
        ("Resume las condiciones generales", "condiciones-generales.md"),
        ("Resume la política de facturación", "facturacion-y-cobranza.md"),
        ("Resume el convenio de flotilla", "carga/x/convenio-flotilla.pdf"),
        ("Resume el convenio de viajes", "carga/x/convenio-viajes.pdf"),
    ],
)
def test_a_named_document_is_resolved(pregunta: str, esperado: str):
    assert elegido(pregunta) == esperado


def test_one_distinctive_word_is_enough():
    """"Carátula" está en un solo título: nombrarla ya lo señala.

    Contar cuánto del título se nombró exigiría decirlo entero. Lo que decide es
    cuánto distingue lo que se dijo, no cuánto se dijo.
    """
    assert elegido("Resume la carátula") == "caratula-logistica-pacifico.md"


def test_accents_and_case_do_not_matter():
    assert elegido("RESUME LA CARATULA") == "caratula-logistica-pacifico.md"


def test_the_filename_also_counts():
    """A veces el título formal no es como la gente llama al documento."""
    assert elegido("resume el archivo de facturacion y cobranza") == "facturacion-y-cobranza.md"


# --- prefiere preguntar a acertar por sorteo ----------------------------------


def test_a_word_shared_by_several_documents_asks_which():
    """"Convenio" está en dos títulos: por sí sola no distingue nada."""
    nombre, opciones = resolver_documento("Resume el convenio", CORPUS)

    assert nombre == ""
    assert len(opciones) == 2
    assert all("Convenio" in o for o in opciones)


def test_the_candidates_are_named_so_the_person_can_choose():
    assert "Convenio de flotilla vehicular 2026" in candidatos("Resume el convenio")


# --- no inventa un referente ---------------------------------------------------


@pytest.mark.parametrize(
    "pregunta",
    ["Dame un resumen del documento", "Resume esto", "Resúmelo", "hazme un resumen"],
)
def test_a_question_that_names_nothing_resolves_to_nothing(pregunta: str):
    """Sin referente no se elige el primero de la lista: se escala."""
    assert resolver_documento(pregunta, CORPUS) == ("", [])


def test_numbers_and_extensions_are_not_names():
    """Nadie pide un documento por su año ni por su extensión."""
    assert elegido("resume el 2026") == ""
    assert elegido("resume el pdf") == ""


def test_an_empty_corpus_resolves_to_nothing():
    assert resolver_documento("Resume la carátula", []) == ("", [])


def test_an_empty_question_resolves_to_nothing():
    assert resolver_documento("   ", CORPUS) == ("", [])


def test_only_the_documents_offered_can_be_chosen():
    """El resolutor elige entre lo que se le pasa, que ya viene filtrado por
    permisos. No hay forma de nombrar algo que no esté en esa lista."""
    solo_publicos = [d for d in CORPUS if not d.nombre.startswith("carga/")]

    assert resolver_documento("Resume el convenio de flotilla", solo_publicos)[0] == ""


def test_a_clear_leader_wins_even_without_the_full_title():
    """"Las condiciones" señala las condiciones generales, no el anexo que
    también las menciona: destaca lo bastante sobre el segundo."""
    assert elegido("Resume las condiciones") == "condiciones-generales.md"


def test_the_margin_is_what_protects_from_choosing_wrong():
    """Dos documentos igual de compatibles no producen una elección, produzcan
    la puntuación que produzcan."""
    empatados = [
        Doc("a.md", "Convenio de flotilla"),
        Doc("b.md", "Convenio de flotilla"),
    ]

    nombre, opciones = resolver_documento("Resume el convenio de flotilla", empatados)

    assert nombre == ""
    assert len(opciones) == 2
