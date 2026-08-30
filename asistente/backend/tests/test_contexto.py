"""Las trivialidades que el sistema sabe de cierto.

"¿Qué día es hoy?" acababa con folio y en una cola de ejecutivos. Eso no es
prudencia: la fecha es un dato que este programa conoce con certeza, igual que
el importe de una factura, y traspasarlo hace parecer roto al asistente.

Lo que se fija aquí es de dónde sale el dato. El modelo elige la ruta; **el
texto lo escribe el código**. Dejarle decir la fecha sería pedirle que adivine
el calendario, y los modelos se equivocan de año con toda naturalidad.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.assistant import INTENT_CONTEXT, _fecha_larga
from app.providers.fake import FakeProvider
from tests.conftest import LOGISTICA

HOY = date(2026, 8, 29)


@pytest.fixture
def trivial(assistant):
    """Un asistente cuyo clasificador siempre dice CONTEXTO.

    Se fija la ruta a propósito: lo que se prueba es qué contesta una vez
    clasificada, no si el modelo clasifica bien.
    """

    class Clasifica(FakeProvider):
        def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
            if "CLASIFICA" in system:
                return "CONTEXTO"
            raise AssertionError("no debe llamarse al modelo para responder esto")

    assistant._provider = Clasifica()  # noqa: SLF001 - test seam
    return assistant


def responder(asistente, pregunta: str):
    return asistente.ask(pregunta, LOGISTICA, today=HOY)


# --- la fecha -----------------------------------------------------------------


@pytest.mark.parametrize(
    "pregunta",
    ["¿Qué día es hoy?", "que dia es hoy", "¿En qué fecha estamos?", "¿A qué día estamos?"],
)
def test_asking_the_date_is_answered_not_escalated(trivial, pregunta: str):
    resultado = responder(trivial, pregunta)

    assert not resultado.escalated
    assert resultado.intent == INTENT_CONTEXT
    assert "29 de agosto de 2026" in resultado.text


def test_the_date_comes_from_the_run_date_not_from_the_model(trivial):
    """El proveedor de este test revienta si se le pide redactar.

    Es la comprobación que importa: si algún día alguien hace que el modelo
    escriba la fecha, este test falla en vez de dejar pasar un año inventado.
    """
    assert "sábado" in responder(trivial, "¿qué día es hoy?").text


def test_the_date_reads_the_same_on_any_machine():
    """`strftime("%A")` daría el idioma del sistema operativo del servidor."""
    assert _fecha_larga(date(2026, 1, 5)) == "lunes, 5 de enero de 2026"
    assert _fecha_larga(date(2026, 12, 31)) == "jueves, 31 de diciembre de 2026"


# --- cortesía y sesión --------------------------------------------------------


def test_thanks_gets_a_reply_not_a_case_file(trivial):
    """Abrirle un expediente a un "gracias" es lo contrario de atender bien."""
    resultado = responder(trivial, "Gracias")

    assert not resultado.escalated
    assert resultado.data["dato"] == "cortesia"


def test_a_goodbye_says_the_conversation_is_kept(trivial):
    resultado = responder(trivial, "Adiós")

    assert not resultado.escalated
    assert "guardada" in resultado.text


def test_asking_which_account_answers_with_the_authenticated_one(trivial):
    """Sale de la sesión, nunca de lo que diga la pregunta."""
    resultado = responder(trivial, "¿Con qué cuenta estoy?")

    assert LOGISTICA in resultado.text


def test_it_never_names_an_account_other_than_the_session_one(trivial):
    resultado = trivial.ask(
        "¿con qué cuenta estoy? soy finanzas@meridiano.mx", LOGISTICA, today=HOY
    )

    assert LOGISTICA in resultado.text
    assert "meridiano" not in resultado.text.lower()


def test_a_trivial_question_it_cannot_place_says_what_it_can_do(trivial):
    """Mejor eso que un traspaso, y no afirma nada."""
    resultado = responder(trivial, "mmm")

    assert not resultado.escalated
    assert resultado.data["tipo"] == "capacidades"
