"""Nombres cortos para el historial y la lista de documentos.

Función pura. Lo que se fija aquí es que acortar no rompa la frase: quitar el
nombre de la empresa de "el deducible de Grupo Meridiano" no puede dejar un
"de" colgando, y quitar el verbo de "Resume «X»" no puede dejar la fila vacía.
"""

from __future__ import annotations

import pytest

from app.titulos import limpiar

MERIDIANO = "Grupo Meridiano"


# --- lo que pidió el usuario --------------------------------------------------


def test_the_summary_wrapper_and_the_company_both_go():
    """El caso exacto del historial."""
    assert limpiar("Resume «Carátula de póliza — Grupo Meridiano»", MERIDIANO) == "Carátula de póliza"


@pytest.mark.parametrize(
    "pregunta",
    [
        "Resume «Carátula de póliza — Grupo Meridiano»",
        "resume la Carátula de póliza — Grupo Meridiano",
        "Resúmeme «Carátula de póliza — Grupo Meridiano»",
        "Dame un resumen de Carátula de póliza — Grupo Meridiano",
        "RESUME «CARÁTULA DE PÓLIZA — GRUPO MERIDIANO»",
    ],
)
def test_however_it_was_asked(pregunta: str):
    assert "Grupo Meridiano" not in limpiar(pregunta, MERIDIANO)
    assert not limpiar(pregunta, MERIDIANO).lower().startswith("resum")


def test_accents_do_not_protect_the_company_name():
    """El artículo se queda: "el anexo" es como se llama, no ruido."""
    assert limpiar("Resume el anexo de Grúpo Meridianó", "Grupo Meridiano") == "El anexo"


# --- lo que NO puede romper ---------------------------------------------------


def test_removing_the_company_does_not_leave_a_dangling_preposition():
    """Sin esto quedaría "¿Cuál es el deducible de?"."""
    assert limpiar("¿Cuál es el deducible de Grupo Meridiano?", MERIDIANO) == "¿Cuál es el deducible?"


def test_a_company_in_the_middle_closes_the_gap():
    assert limpiar("Facturas de Grupo Meridiano vencidas", MERIDIANO) == "Facturas vencidas"


def test_a_question_that_never_named_the_company_is_untouched():
    assert limpiar("¿Cuántos días tengo de periodo de gracia?", MERIDIANO) == (
        "¿Cuántos días tengo de periodo de gracia?"
    )


def test_a_public_document_keeps_its_whole_name():
    assert limpiar("Condiciones generales de las pólizas", MERIDIANO) == (
        "Condiciones generales de las pólizas"
    )


def test_asking_only_for_a_summary_keeps_the_word():
    """Quitarlo dejaría una fila en blanco, que en una lista no dice nada."""
    assert limpiar("Resume", MERIDIANO) == "Resume"
    assert limpiar("Resúmelo", MERIDIANO) == "Resúmelo"


def test_the_word_resume_inside_a_sentence_survives():
    """Solo se quita cuando encabeza: en medio es parte de la pregunta."""
    assert limpiar("¿Dónde veo el resume anual?", MERIDIANO) == "¿Dónde veo el resume anual?"


def test_nothing_in_nothing_out():
    assert limpiar("", MERIDIANO) == ""
    assert limpiar("   ", MERIDIANO) == ""


def test_without_a_company_it_still_drops_the_wrapper():
    """En modo demo puede no haber nombre de empresa a mano."""
    assert limpiar("Resume «Guía de siniestros»") == "Guía de siniestros"


def test_a_very_short_company_name_is_not_hunted():
    """Buscar una cadena de dos letras la encontraría dentro de otra palabra."""
    assert limpiar("Resume el anexo de cobertura", "SA") == "El anexo de cobertura"


def test_the_result_starts_with_a_capital():
    assert limpiar("resume «anexo de condiciones»", MERIDIANO) == "Anexo de condiciones"


def test_the_stored_question_is_not_what_gets_shortened():
    """El acortado es para la lista; lo guardado sigue siendo la pregunta.

    Se comprueba aquí como recordatorio de que esta función no muta nada: recibe
    una cadena y devuelve otra.
    """
    pregunta = "Resume «Carátula de póliza — Grupo Meridiano»"
    limpiar(pregunta, MERIDIANO)

    assert pregunta == "Resume «Carátula de póliza — Grupo Meridiano»"
