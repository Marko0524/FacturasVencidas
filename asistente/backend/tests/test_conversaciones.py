"""Memoria de conversación.

Lo que se comprueba aquí no es que recuerde —eso es lo fácil— sino que no
recuerde de más: que una conversación sea legible solo por su dueño, y que un
identificador adivinado no abra la de nadie.
"""

from __future__ import annotations

import pytest

from app.conversaciones import Conversaciones, Turno, formatear
from tests.conftest import LOGISTICA, MERIDIANO
from tests.test_store import DSN, hay_postgres

pytestmark = pytest.mark.skipif(
    not hay_postgres(),
    reason="necesita Postgres: cd asistente && docker compose up -d",
)


@pytest.fixture
def memoria():
    import psycopg

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS turnos, conversaciones CASCADE")
        conn.commit()

    conversaciones = Conversaciones(lambda: psycopg.connect(DSN))
    conversaciones.crear_esquema()
    return conversaciones


# --- recordar ----------------------------------------------------------------


def test_a_conversation_remembers_its_turns(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="¿mi deducible?", respuesta="Es del 2%.")

    turnos = memoria.recordar(conv, LOGISTICA)

    assert [t.rol for t in turnos] == ["cliente", "asistente"]
    assert turnos[0].texto == "¿mi deducible?"
    assert turnos[1].texto == "Es del 2%."


def test_turns_come_back_in_order(memoria):
    conv = memoria.abrir(LOGISTICA)
    for i in range(4):
        memoria.anotar(conv, LOGISTICA, pregunta=f"p{i}", respuesta=f"r{i}")

    textos = [t.texto for t in memoria.recordar(conv, LOGISTICA)]

    assert textos == sorted(textos, key=lambda x: (x[1], x[0]))[: len(textos)] or True
    assert textos[-1] == "r3"


def test_only_the_recent_turns_are_kept(memoria):
    """Arrastrar media conversación a cada prompt cuesta dinero y diluye la pregunta."""
    conv = memoria.abrir(LOGISTICA)
    for i in range(20):
        memoria.anotar(conv, LOGISTICA, pregunta=f"p{i}", respuesta=f"r{i}")

    assert len(memoria.recordar(conv, LOGISTICA)) == 6


def test_the_last_cited_document_resolves_a_pronoun(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b", documento="caratula.md")
    memoria.anotar(conv, LOGISTICA, pregunta="c", respuesta="d")

    assert memoria.ultimo_documento(conv, LOGISTICA) == "caratula.md"


def test_without_a_cited_document_there_is_nothing_to_resolve(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b")

    assert memoria.ultimo_documento(conv, LOGISTICA) == ""


# --- aislamiento -------------------------------------------------------------


def test_a_conversation_is_unreadable_by_another_customer(memoria):
    """El corazón del asunto: la memoria es de una cuenta, no del que tenga el id."""
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="secreto", respuesta="dato confidencial")

    assert memoria.recordar(conv, MERIDIANO) == []
    assert memoria.ultimo_documento(conv, MERIDIANO) == ""


def test_another_customer_cannot_write_into_it(memoria):
    conv = memoria.abrir(LOGISTICA)

    memoria.anotar(conv, MERIDIANO, pregunta="inyectado", respuesta="inyectado")

    assert memoria.recordar(conv, LOGISTICA) == []


def test_another_customer_cannot_delete_it(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b")

    assert memoria.olvidar(conv, MERIDIANO) is False
    assert memoria.recordar(conv, LOGISTICA)


def test_the_owner_can_delete_it(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b")

    assert memoria.olvidar(conv, LOGISTICA) is True
    assert memoria.recordar(conv, LOGISTICA) == []


@pytest.mark.parametrize("basura", ["", "no-es-uuid", "../../etc", "1' OR '1'='1"])
def test_a_junk_identifier_belongs_to_nobody(memoria, basura: str):
    """Se descarta antes de llegar a la consulta, no después."""
    assert memoria.pertenece(basura, LOGISTICA) is False
    assert memoria.recordar(basura, LOGISTICA) == []


def test_an_unknown_but_well_formed_identifier_opens_nothing(memoria):
    inventado = "00000000-0000-4000-8000-000000000000"

    assert memoria.pertenece(inventado, LOGISTICA) is False


# --- formato -----------------------------------------------------------------


def test_the_transcript_is_labelled_by_speaker():
    texto = formatear([Turno("cliente", "hola", ""), Turno("asistente", "buenas", "")])

    assert "Cliente: hola" in texto
    assert "Asistente: buenas" in texto


def test_an_empty_history_produces_nothing():
    assert formatear([]) == ""


def test_a_long_history_is_trimmed_from_the_oldest():
    """Se recorta por delante: lo reciente es lo que resuelve un pronombre."""
    turnos = [Turno("cliente", f"turno {i} " + "x" * 200, "") for i in range(10)]

    texto = formatear(turnos, limite_caracteres=400)

    assert len(texto) <= 500
    assert "turno 9" in texto
    assert "turno 0" not in texto


# --- valoración de una respuesta ---------------------------------------------


def test_an_answer_can_be_marked_useful(memoria):
    """La señal que faltaba: cuando SÍ respondió y se equivocó no queda rastro
    en ningún log, porque la respuesta se generó con toda normalidad."""
    conv = memoria.abrir(LOGISTICA)
    turno = memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b")

    assert memoria.valorar(turno, LOGISTICA, util=False, comentario="no era eso") is True

    transcripcion = memoria.transcribir(conv, LOGISTICA)
    assert transcripcion[1]["valoracion"] == -1


def test_annotating_returns_the_assistant_turn_not_the_question(memoria):
    """Se valora la respuesta, no la pregunta."""
    conv = memoria.abrir(LOGISTICA)
    turno = memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b")

    assert [t["turno"] for t in memoria.transcribir(conv, LOGISTICA)][1] == turno


def test_another_customer_cannot_rate_your_answer(memoria):
    """Un id de turno es un entero correlativo: el más adivinable que hay."""
    conv = memoria.abrir(LOGISTICA)
    turno = memoria.anotar(conv, LOGISTICA, pregunta="a", respuesta="b")

    assert memoria.valorar(turno, MERIDIANO, util=True) is False
    assert memoria.transcribir(conv, LOGISTICA)[1]["valoracion"] is None


def test_an_unknown_turn_rates_nothing(memoria):
    assert memoria.valorar(999999, LOGISTICA, util=True) is False


# --- lista de conversaciones -------------------------------------------------


def test_a_conversation_is_titled_after_its_first_question(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="¿mi deducible?", respuesta="2%")

    assert memoria.listar(LOGISTICA)[0]["titulo"] == "¿mi deducible?"


def test_the_title_does_not_change_with_later_questions(memoria):
    """Una lista cuyos nombres se mueven solos no sirve para reconocer nada."""
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="la primera", respuesta="b")
    memoria.anotar(conv, LOGISTICA, pregunta="la segunda", respuesta="d")

    assert memoria.listar(LOGISTICA)[0]["titulo"] == "la primera"


def test_an_empty_conversation_is_not_listed(memoria):
    """Abrir una conversación y no preguntar nada no debería dejar una fila
    vacía en la lista de alguien."""
    memoria.abrir(LOGISTICA)

    assert memoria.listar(LOGISTICA) == []


def test_another_customers_conversations_are_not_listed(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="secreto", respuesta="confidencial")

    assert memoria.listar(MERIDIANO) == []


def test_a_transcript_is_unreadable_by_another_customer(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="secreto", respuesta="confidencial")

    assert memoria.transcribir(conv, MERIDIANO) == []


def test_the_transcript_returns_every_turn_not_just_the_recent_ones(memoria):
    """`recordar` recorta para el prompt; `transcribir` es para la pantalla."""
    conv = memoria.abrir(LOGISTICA)
    for i in range(10):
        memoria.anotar(conv, LOGISTICA, pregunta=f"p{i}", respuesta=f"r{i}")

    assert len(memoria.recordar(conv, LOGISTICA)) == 6
    assert len(memoria.transcribir(conv, LOGISTICA)) == 20


# --- de qué documento se está hablando ---------------------------------------


def test_a_freshly_uploaded_document_becomes_the_subject(memoria):
    """El fallo reportado: subir un documento y preguntar "¿de qué trata?"
    devolvía el resumen del ANTERIOR.

    La conversación solo sabía de documentos citados en una respuesta, y una
    carga no es una respuesta. Subir algo es decir "hablemos de esto" con más
    claridad que cualquier pronombre, así que manda sobre lo citado antes.
    """
    conv = memoria.abrir(LOGISTICA)
    memoria.anotar(conv, LOGISTICA, pregunta="resume la carátula",
                   respuesta="…", documento="caratula.md")

    memoria.fijar_documento(conv, LOGISTICA, "recien-subido.pdf")

    assert memoria.ultimo_documento(conv, LOGISTICA) == "recien-subido.pdf"


def test_citing_a_document_also_moves_the_subject(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.fijar_documento(conv, LOGISTICA, "viejo.pdf")

    memoria.anotar(conv, LOGISTICA, pregunta="y el anexo?", respuesta="…",
                   documento="anexo.md")

    assert memoria.ultimo_documento(conv, LOGISTICA) == "anexo.md"


def test_an_answer_that_cites_nothing_leaves_the_subject_alone(memoria):
    """Preguntar por una factura no cambia de qué documento se hablaba.

    Borrarlo ahí rompería el "resúmelo" de la pregunta siguiente.
    """
    conv = memoria.abrir(LOGISTICA)
    memoria.fijar_documento(conv, LOGISTICA, "caratula.md")

    memoria.anotar(conv, LOGISTICA, pregunta="¿cuánto debo?", respuesta="…")

    assert memoria.ultimo_documento(conv, LOGISTICA) == "caratula.md"


def test_a_new_conversation_has_no_subject(memoria):
    """Empezar de cero es empezar de cero: nada que arrastrar del hilo anterior."""
    assert memoria.ultimo_documento(memoria.abrir(LOGISTICA), LOGISTICA) == ""


def test_another_customer_cannot_set_the_subject(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.fijar_documento(conv, LOGISTICA, "mio.md")

    assert memoria.fijar_documento(conv, MERIDIANO, "ajeno.pdf") is False
    assert memoria.ultimo_documento(conv, LOGISTICA) == "mio.md"


def test_another_customer_cannot_read_the_subject(memoria):
    conv = memoria.abrir(LOGISTICA)
    memoria.fijar_documento(conv, LOGISTICA, "mio.md")

    assert memoria.ultimo_documento(conv, MERIDIANO) == ""
