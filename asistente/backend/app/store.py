"""Vector store on Postgres + pgvector.

This is where the design's central claim stops being a promise and becomes a
``WHERE`` clause. The README said that with a real index "what would change is
where the filter is expressed, not that it comes first" — here it is expressed
as part of the query that ranks, so the database itself never even scores a
fragment the caller may not see. There is no code path that could forget.

Two deliberate scope choices, both stated rather than hidden:

* **No ANN index.** pgvector's HNSW and IVFFlat cap out at 2000 dimensions and
  ``gemini-embedding-001`` returns 3072, so an exact scan is what fits. With a
  few hundred fragments it is also faster than an approximate index. At corpus
  scale you either reduce the output dimensionality or move to ``halfvec``.
* **The vector column is sized on first ingest.** Providers disagree about
  dimensions (3072, 1536, 768) and a column cannot be polymorphic, so the
  dimension is recorded and a mismatch is refused loudly instead of producing
  silently meaningless distances.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.retrieval import SCOPE_CUSTOMER, SCOPE_PUBLIC, Chunk, ScoredChunk

logger = logging.getLogger(__name__)

ORIGIN_CORPUS = "corpus"
ORIGIN_UPLOAD = "carga"


class StoreError(Exception):
    """The store could not be reached or is in an unusable state."""


@dataclass(frozen=True)
class DocumentSummary:
    """What the UI shows in the document list."""

    nombre: str
    titulo: str
    alcance: str
    cliente: str
    origen: str
    fragmentos: int
    creado_en: str

    def as_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "titulo": self.titulo,
            "alcance": self.alcance,
            "cliente": self.cliente,
            "origen": self.origen,
            "fragmentos": self.fragmentos,
            "creado_en": self.creado_en,
        }


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documentos (
    id         BIGSERIAL PRIMARY KEY,
    nombre     TEXT NOT NULL UNIQUE,
    titulo     TEXT NOT NULL,
    alcance    TEXT NOT NULL CHECK (alcance IN ('publico', 'cliente')),
    cliente    TEXT NOT NULL DEFAULT '',
    origen     TEXT NOT NULL,
    -- El archivo tal cual se subió, en binario. Reconstruirlo desde los
    -- fragmentos daría algo parecido pero distinto, y un PDF no se reconstruye
    -- en absoluto: lo que se indexa es su texto, no el archivo.
    archivo    BYTEA NOT NULL DEFAULT ''::bytea,
    medio      TEXT NOT NULL DEFAULT 'application/pdf',
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Un documento de cliente sin dueño no sería autorizable. La base lo
    -- rechaza, en vez de confiar en que la aplicación se acuerde.
    CONSTRAINT alcance_cliente_tiene_dueno
        CHECK (alcance <> 'cliente' OR cliente <> '')
);

-- Para bases creadas antes de que existiera la columna. `IF NOT EXISTS` la
-- hace idempotente, así que arrancar dos veces no es un problema.
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS archivo BYTEA NOT NULL DEFAULT ''::bytea;
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS medio TEXT NOT NULL DEFAULT 'application/pdf';
ALTER TABLE documentos DROP COLUMN IF EXISTS contenido;

CREATE INDEX IF NOT EXISTS documentos_cliente_idx ON documentos (lower(cliente));

CREATE TABLE IF NOT EXISTS fragmentos (
    id           BIGSERIAL PRIMARY KEY,
    documento_id BIGINT NOT NULL REFERENCES documentos (id) ON DELETE CASCADE,
    ordinal      INT NOT NULL,
    texto        TEXT NOT NULL,
    embedding    VECTOR({dimension}) NOT NULL,
    UNIQUE (documento_id, ordinal)
);

CREATE TABLE IF NOT EXISTS metadatos (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""

# El recorte de permisos, escrito una sola vez y aplicado por la base.
VISIBLE = """
    (d.alcance = 'publico' OR (d.alcance = 'cliente' AND lower(d.cliente) = lower(%s)))
"""


class PostgresVectorStore:
    """Documents and fragments in Postgres, searched by cosine distance."""

    def __init__(self, dsn: str, provider, *, top_k: int = 4, min_similarity: float = 0.55):
        self._dsn = dsn
        self._provider = provider
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._dimension: int | None = None

    # --- lifecycle -----------------------------------------------------------

    def connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise StoreError("psycopg is required: pip install -r requirements.txt") from exc
        try:
            return psycopg.connect(self._dsn)
        except Exception as exc:  # noqa: BLE001 - psycopg raises broadly
            raise StoreError(f"could not connect to Postgres: {exc}") from exc

    def initialise(self, dimension: int) -> None:
        """Create the schema, sized for this provider's embeddings."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT valor FROM metadatos WHERE clave = 'dimension'"
                        if _table_exists(cur, "metadatos") else "SELECT NULL WHERE false")
            row = cur.fetchone()
            existing = int(row[0]) if row and row[0] else None

            if existing is not None and existing != dimension:
                raise StoreError(
                    f"the store holds {existing}-dimension vectors and this provider "
                    f"produces {dimension}. Mixing them would make every distance "
                    f"meaningless. Re-ingest with one provider, or drop the volume."
                )

            cur.execute(SCHEMA.format(dimension=dimension))
            cur.execute(
                "INSERT INTO metadatos (clave, valor) VALUES ('dimension', %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                (str(dimension),),
            )
            conn.commit()

        self._dimension = dimension
        logger.info("Store ready dimension=%d", dimension)

    def ensure_ready(self) -> None:
        """Discover the dimension from the provider, then build the schema."""
        if self._dimension is not None:
            return
        probe = self._provider.embed(["dimensión"])[0]
        self.initialise(len(probe))

    # --- writing -------------------------------------------------------------

    def upsert_document(
        self,
        *,
        nombre: str,
        titulo: str,
        alcance: str,
        cliente: str,
        origen: str,
        textos: list[str],
        archivo: bytes = b"",
        medio: str = "application/pdf",
    ) -> int:
        """Replace a document and its fragments. Returns how many were stored.

        Replace, not append: re-uploading a corrected file must not leave the
        old paragraphs behind to be retrieved as if they were still true.
        """
        if alcance not in (SCOPE_PUBLIC, SCOPE_CUSTOMER):
            raise StoreError(f"unknown scope: {alcance}")
        if alcance == SCOPE_CUSTOMER and not cliente:
            raise StoreError("a customer document needs an owner")
        if not textos:
            raise StoreError("the document has no readable text")

        self.ensure_ready()
        vectores = self._provider.embed(textos)

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documentos
                       (nombre, titulo, alcance, cliente, origen, archivo, medio)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (nombre) DO UPDATE
                    SET titulo = EXCLUDED.titulo,
                        alcance = EXCLUDED.alcance,
                        cliente = EXCLUDED.cliente,
                        origen = EXCLUDED.origen,
                        archivo = EXCLUDED.archivo,
                        medio = EXCLUDED.medio,
                        creado_en = now()
                RETURNING id
                """,
                (nombre, titulo, alcance, cliente, origen, archivo, medio),
            )
            documento_id = cur.fetchone()[0]
            cur.execute("DELETE FROM fragmentos WHERE documento_id = %s", (documento_id,))

            for ordinal, (texto, vector) in enumerate(zip(textos, vectores)):
                cur.execute(
                    "INSERT INTO fragmentos (documento_id, ordinal, texto, embedding) "
                    "VALUES (%s, %s, %s, %s::vector)",
                    (documento_id, ordinal, texto, _vector_literal(vector)),
                )
            conn.commit()

        logger.info(
            "Document stored nombre=%s alcance=%s fragmentos=%d", nombre, alcance, len(textos)
        )
        return len(textos)

    def document_fragments(self, nombre: str, customer_email: str) -> list[Chunk]:
        """Todos los fragmentos de un documento, en orden.

        Un resumen no es una búsqueda por similitud: pide el documento entero,
        no los cuatro trozos que más se parecen a la pregunta. Este es el camino
        distinto que eso necesita, con el mismo predicado de permisos —resumir
        es otra forma de leer, y no puede tener otra frontera.
        """
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.nombre, d.titulo, d.alcance, d.cliente, f.ordinal, f.texto
                  FROM fragmentos f
                  JOIN documentos d ON d.id = f.documento_id
                 WHERE d.nombre = %s AND {VISIBLE}
                 ORDER BY f.ordinal
                """,
                (nombre, customer_email),
            )
            filas = cur.fetchall()

        base = nombre.rsplit(".", 1)[0]
        return [
            Chunk(
                id=f"{base}#{ordinal}",
                document=doc,
                title=titulo,
                scope=alcance,
                customer=cliente,
                text=texto,
            )
            for doc, titulo, alcance, cliente, ordinal, texto in filas
        ]

    def read_document(self, nombre: str, customer_email: str) -> tuple[str, bytes, str] | None:
        """El original de un documento **que este cliente puede ver**.

        Devuelve ``None`` si no lo puede ver o no existe, y el archivo vacío si
        lo puede ver pero no se guardó.

        El mismo predicado de visibilidad que usa la búsqueda, aplicado en la
        consulta. Descargar es otra forma de leer, así que no puede tener una
        frontera distinta a la de preguntar.
        """
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.titulo, d.archivo, d.medio
                  FROM documentos d
                 WHERE d.nombre = %s AND {VISIBLE}
                """,
                (nombre, customer_email),
            )
            fila = cur.fetchone()

        # Se devuelve tal cual, incluido el caso de contenido vacío. Quien llama
        # necesita poder distinguir "no es tuyo o no existe" —una sola respuesta,
        # para no confirmar documentos ajenos— de "es tuyo pero no guardamos el
        # original", que sí merece una explicación porque la persona lo está
        # viendo en su lista y no entiende por qué no baja.
        if fila is None:
            return None
        titulo, archivo, medio = fila
        return titulo, bytes(archivo or b""), medio

    def delete_document(self, nombre: str, customer_email: str) -> bool:
        """Delete a document **the caller owns**. Public ones are not theirs to remove."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM documentos WHERE nombre = %s AND alcance = 'cliente' "
                "AND lower(cliente) = lower(%s)",
                (nombre, customer_email),
            )
            borrados = cur.rowcount
            conn.commit()
        return borrados > 0

    # --- reading -------------------------------------------------------------

    def list_documents(self, customer_email: str) -> list[DocumentSummary]:
        """Everything this caller may see, and nothing else."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.nombre, d.titulo, d.alcance, d.cliente, d.origen,
                       count(f.id), to_char(d.creado_en, 'YYYY-MM-DD HH24:MI')
                  FROM documentos d
                  LEFT JOIN fragmentos f ON f.documento_id = d.id
                 WHERE {VISIBLE}
                 GROUP BY d.id
                 ORDER BY d.alcance, d.nombre
                """,
                (customer_email,),
            )
            return [DocumentSummary(*fila) for fila in cur.fetchall()]

    def search(self, question: str, customer_email: str) -> list[ScoredChunk]:
        """Rank only what the caller is allowed to see.

        The permission predicate sits inside the same query that orders by
        distance, so an unauthorised fragment is never a candidate — it is not
        retrieved and then discarded, the database never considers it.
        """
        self.ensure_ready()
        vector = _vector_literal(self._provider.embed([question])[0])

        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.nombre, d.titulo, d.alcance, d.cliente, f.ordinal, f.texto,
                       1 - (f.embedding <=> %s::vector) AS similitud
                  FROM fragmentos f
                  JOIN documentos d ON d.id = f.documento_id
                 WHERE {VISIBLE}
                 ORDER BY f.embedding <=> %s::vector
                 LIMIT %s
                """,
                (vector, customer_email, vector, self._top_k),
            )
            filas = cur.fetchall()

        resultados = []
        for nombre, titulo, alcance, cliente, ordinal, texto, similitud in filas:
            if similitud < self._min_similarity:
                continue
            base = nombre.rsplit(".", 1)[0]
            resultados.append(
                ScoredChunk(
                    Chunk(
                        id=f"{base}#{ordinal}",
                        document=nombre,
                        title=titulo,
                        scope=alcance,
                        customer=cliente,
                        text=texto,
                    ),
                    float(similitud),
                )
            )
        return resultados


def _vector_literal(values: list[float]) -> str:
    """pgvector's text input format: ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _table_exists(cur, nombre: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{nombre}",))
    return bool(cur.fetchone()[0])
