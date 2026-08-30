"""Escalamientos guardados.

Lo que importa aquí es lo mismo que en la memoria de conversación: que un caso
pertenezca a una cuenta. Un folio es corto y derivado del caso —no es un
secreto— así que adivinar uno no puede bastar para leer ni escribir en él.
"""

from __future__ import annotations

import pytest

from app.escalamientos import Escalamientos
from tests.conftest import LOGISTICA, MERIDIANO
from tests.test_store import DSN, hay_postgres

pytestmark = pytest.mark.skipif(
    not hay_postgres(),
    reason="necesita Postgres: cd asistente && docker compose up -d",
)


@pytest.fixture
def casos():
    import psycopg

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS escalamientos CASCADE")
        conn.commit()

    registro = Escalamientos(lambda: psycopg.connect(DSN))
    registro.crear_esquema()
    return registro


def registrar(casos, folio="ESC-20260829-A317", cliente=LOGISTICA):
    casos.registrar(
        folio, cliente, intencion="HUMANO", pregunta="reportar un choque",
        motivo="la consulta requiere atención humana",
    )
    return folio


# --- que exista ---------------------------------------------------------------


def test_an_escalation_is_actually_stored(casos):
    """Antes el folio se escribía en el log y se tiraba: la respuesta prometía
    un seguimiento que no existía en ninguna parte."""
    folio = registrar(casos)

    caso = casos.detallar(folio, LOGISTICA)

    assert caso is not None
    assert caso.pregunta == "reportar un choque"
    assert caso.estado == "abierto"


def test_the_customer_can_list_their_own_cases(casos):
    registrar(casos, "ESC-1")
    registrar(casos, "ESC-2")

    assert {c.folio for c in casos.listar(LOGISTICA)} == {"ESC-1", "ESC-2"}


def test_registering_the_same_case_twice_does_not_duplicate_it(casos):
    """El folio se deriva del caso, así que repetir la consulta lo repite."""
    folio = registrar(casos)
    registrar(casos, folio)

    assert len(casos.listar(LOGISTICA)) == 1


def test_re_registering_does_not_wipe_the_contact_already_given(casos):
    """Sería lo que pasaría con un UPSERT, y borraría lo único que aporta la persona."""
    folio = registrar(casos)
    casos.anotar_contacto(folio, LOGISTICA, contacto="55 1234 5678", nota="por la mañana")

    registrar(casos, folio)

    assert casos.detallar(folio, LOGISTICA).contacto == "55 1234 5678"


# --- contacto -----------------------------------------------------------------


def test_the_customer_can_say_how_to_be_reached(casos):
    folio = registrar(casos)

    assert casos.anotar_contacto(folio, LOGISTICA, contacto="55 1234 5678", nota="urge") is True

    caso = casos.detallar(folio, LOGISTICA)
    assert caso.contacto == "55 1234 5678"
    assert caso.nota == "urge"


# --- aislamiento --------------------------------------------------------------


def test_another_customer_cannot_read_the_case(casos):
    folio = registrar(casos)

    assert casos.detallar(folio, MERIDIANO) is None
    assert casos.listar(MERIDIANO) == []


def test_another_customer_cannot_write_a_contact_into_it(casos):
    """El corazón del asunto: el folio no es una credencial."""
    folio = registrar(casos)

    assert casos.anotar_contacto(folio, MERIDIANO, contacto="ajeno", nota="") is False
    assert casos.detallar(folio, LOGISTICA).contacto == ""


def test_an_unknown_folio_writes_nothing(casos):
    assert casos.anotar_contacto("ESC-INVENTADO", LOGISTICA, contacto="x", nota="") is False


def test_the_reason_never_reaches_the_customer(casos):
    """`motivo` dice por qué falló, y a veces eso es "la factura no está en la
    cuenta del cliente". Es para quien atienda el caso, no para el cliente."""
    folio = registrar(casos)

    assert "motivo" not in casos.detallar(folio, LOGISTICA).as_dict()
