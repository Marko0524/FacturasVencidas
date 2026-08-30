"""Postgres + pgvector.

These are integration tests: they need the database from ``docker-compose.yml``
and they skip, loudly, when it is not there. Faking a vector store would test
the fake — and the whole claim being verified here is that *the database*
refuses to rank a fragment the caller may not see.

    cd asistente && docker compose up -d
"""

from __future__ import annotations

import os

import pytest

from app.providers.fake import FakeProvider
from app.retrieval import SCOPE_CUSTOMER, SCOPE_PUBLIC
from app.store import PostgresVectorStore, StoreError
from tests.conftest import AURORA, LOGISTICA, MERIDIANO

DSN = os.getenv(
    "TEST_DATABASE_URL", "postgresql://asistente:asistente@localhost:5432/asistente_test"
)

CONVENIO = [
    "## Deducible de flotilla\n\nEl deducible de flotilla es del tres por ciento.",
    "## Asistencia vial\n\nLa asistencia vial opera veinticuatro horas al día.",
]
GENERALES = [
    "## Periodo de gracia\n\nEl periodo de gracia es de treinta días naturales.",
    "## Cancelación\n\nLa cancelación se solicita por escrito.",
]

# Un PDF mínimo con bytes que no sobreviven a un viaje por texto: nulo, 0xFF y
# 0xFE. Si el almacén los devuelve intactos, guarda binario de verdad.
PDF_FALSO = b"%PDF-1.4 binario" + bytes([0, 255, 254])


def hay_postgres() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    base = DSN.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(base, connect_timeout=3) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = 'asistente_test'")
                if not cur.fetchone():
                    cur.execute("CREATE DATABASE asistente_test")
        return True
    except Exception:  # noqa: BLE001 - cualquier fallo significa "no disponible"
        return False


pytestmark = pytest.mark.skipif(
    not hay_postgres(),
    reason="necesita Postgres con pgvector: cd asistente && docker compose up -d",
)


@pytest.fixture
def store():
    import psycopg

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS fragmentos, documentos, metadatos CASCADE")
        conn.commit()

    almacen = PostgresVectorStore(DSN, FakeProvider(), top_k=4, min_similarity=0.05)
    almacen.ensure_ready()
    return almacen


def sembrar(store):
    store.upsert_document(
        nombre="condiciones.md", titulo="Condiciones generales", alcance=SCOPE_PUBLIC,
        cliente="", origen="corpus", textos=GENERALES,
    )
    store.upsert_document(
        nombre="carga/logistica/convenio.pdf", titulo="Convenio de flotilla",
        alcance=SCOPE_CUSTOMER, cliente=LOGISTICA, origen="carga", textos=CONVENIO,
    )


# --- el recorte de permisos, aplicado por la base ----------------------------


def test_the_database_never_ranks_another_customers_fragment(store):
    """El corazón del diseño, ahora como cláusula WHERE."""
    sembrar(store)

    hits = store.search("deducible de flotilla", MERIDIANO)

    assert not any("convenio" in h.chunk.document for h in hits)


def test_the_owner_does_reach_their_own_upload(store):
    sembrar(store)

    hits = store.search("deducible de flotilla tres por ciento", LOGISTICA)

    assert any("convenio" in h.chunk.document for h in hits)


def test_public_documents_reach_everyone(store):
    sembrar(store)

    for cliente in (LOGISTICA, MERIDIANO, AURORA):
        hits = store.search("periodo de gracia treinta días", cliente)
        assert any("condiciones" in h.chunk.document for h in hits)


def test_the_listing_hides_other_customers_documents(store):
    sembrar(store)

    nombres = {d.nombre for d in store.list_documents(MERIDIANO)}

    assert "condiciones.md" in nombres
    assert "carga/logistica/convenio.pdf" not in nombres


# --- escritura ---------------------------------------------------------------


def test_reuploading_replaces_the_old_fragments(store):
    """Los párrafos corregidos no pueden convivir con los que corrigen."""
    sembrar(store)
    store.upsert_document(
        nombre="carga/logistica/convenio.pdf", titulo="Convenio v2",
        alcance=SCOPE_CUSTOMER, cliente=LOGISTICA, origen="carga",
        textos=["## Deducible\n\nEl deducible de flotilla ahora es del cinco por ciento."],
    )

    documento = next(
        d for d in store.list_documents(LOGISTICA) if d.nombre.endswith("convenio.pdf")
    )
    assert documento.fragmentos == 1
    assert documento.titulo == "Convenio v2"


def test_a_customer_document_without_an_owner_is_refused(store):
    with pytest.raises(StoreError, match="owner"):
        store.upsert_document(
            nombre="x.pdf", titulo="X", alcance=SCOPE_CUSTOMER, cliente="",
            origen="carga", textos=["texto"],
        )


def test_a_document_with_no_fragments_is_refused(store):
    with pytest.raises(StoreError, match="no readable text"):
        store.upsert_document(
            nombre="x.pdf", titulo="X", alcance=SCOPE_PUBLIC, cliente="",
            origen="carga", textos=[],
        )


# --- borrado -----------------------------------------------------------------


def test_a_customer_can_delete_their_own_document(store):
    sembrar(store)

    assert store.delete_document("carga/logistica/convenio.pdf", LOGISTICA) is True
    assert store.search("deducible de flotilla", LOGISTICA) == []


def test_a_customer_cannot_delete_someone_elses(store):
    sembrar(store)

    assert store.delete_document("carga/logistica/convenio.pdf", MERIDIANO) is False
    assert store.search("deducible de flotilla tres por ciento", LOGISTICA)


def test_nobody_deletes_the_insurers_own_documents(store):
    """Un documento público no es de nadie en particular, así que no se borra así."""
    sembrar(store)

    assert store.delete_document("condiciones.md", LOGISTICA) is False


# --- dimensiones -------------------------------------------------------------


def test_mixing_providers_with_different_dimensions_is_refused(store):
    """Mezclar vectores de distinto tamaño haría que toda distancia mienta."""
    with pytest.raises(StoreError, match="dimension"):
        store.initialise(768)


# --- descarga del original ---------------------------------------------------


def test_the_original_file_comes_back_byte_for_byte(store):
    """Lo que se descarga es el archivo, no una reconstrucción.

    El PDF de prueba lleva un byte nulo a propósito: si el almacén lo guardara
    como texto, ese byte no sobreviviría al viaje.
    """
    store.upsert_document(
        nombre="carga/logistica/archivo.pdf", titulo="Archivo", alcance=SCOPE_CUSTOMER,
        cliente=LOGISTICA, origen="carga", textos=CONVENIO,
        archivo=PDF_FALSO, medio="application/pdf",
    )

    assert store.read_document("carga/logistica/archivo.pdf", LOGISTICA) == (
        "Archivo", PDF_FALSO, "application/pdf",
    )


def test_downloading_is_scoped_like_searching(store):
    """Descargar es otra forma de leer: no puede tener otra frontera."""
    sembrar(store)
    store.upsert_document(
        nombre="carga/logistica/privado.pdf", titulo="Privado", alcance=SCOPE_CUSTOMER,
        cliente=LOGISTICA, origen="carga", textos=CONVENIO, archivo=PDF_FALSO,
    )

    assert store.read_document("carga/logistica/privado.pdf", LOGISTICA) is not None
    assert store.read_document("carga/logistica/privado.pdf", MERIDIANO) is None


def test_a_visible_document_without_its_original_is_distinguishable(store):
    """No es lo mismo "no es tuyo" que "es tuyo pero no guardamos el archivo".

    Lo primero responde igual que "no existe", para no confirmar documentos
    ajenos. Lo segundo merece explicación: la persona lo está viendo en su lista
    y no entiende por qué no baja.
    """
    sembrar(store)

    titulo, archivo, _ = store.read_document("condiciones.md", LOGISTICA)
    assert (titulo, archivo) == ("Condiciones generales", b"")
    assert store.read_document("no-existe.md", LOGISTICA) is None


def test_reuploading_replaces_the_stored_original(store):
    store.upsert_document(
        nombre="carga/logistica/v.pdf", titulo="V", alcance=SCOPE_CUSTOMER,
        cliente=LOGISTICA, origen="carga", textos=CONVENIO, archivo=b"version uno",
    )
    store.upsert_document(
        nombre="carga/logistica/v.pdf", titulo="V", alcance=SCOPE_CUSTOMER,
        cliente=LOGISTICA, origen="carga", textos=CONVENIO, archivo=b"version dos",
    )

    assert store.read_document("carga/logistica/v.pdf", LOGISTICA)[1] == b"version dos"
