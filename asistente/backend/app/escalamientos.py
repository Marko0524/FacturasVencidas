"""Los escalamientos, guardados.

Hasta ahora el asistente decía "le paso la consulta a un ejecutivo, que le
contactará por este mismo medio", generaba un folio y lo tiraba al log. No había
tabla, ni cola, ni medio: la única promesa sin respaldo en un sistema construido
entero alrededor de no afirmar lo que no puede sostener.

Aquí la promesa se cumple hasta donde puede cumplirse sin un CRM detrás: el caso
queda registrado con su folio, el cliente puede añadir cómo prefiere que le
contacten, y puede volver a consultarlo. Lo que no hay es un humano al otro lado;
por eso el texto de la respuesta ya no promete uno, dice lo que de verdad ocurre.

Como en la memoria de conversación, cada caso pertenece a una cuenta y se
comprueba en cada lectura y en cada escritura.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

ABIERTO = "abierto"
ESTADOS = (ABIERTO, "contactado", "cerrado")

# Cuánto texto se acepta del cliente. Un dato de contacto no ocupa más, y el
# límite evita que una nota sea un vertedero.
MAX_CONTACTO = 200
MAX_NOTA = 1000

ESQUEMA = """
CREATE TABLE IF NOT EXISTS escalamientos (
    folio      TEXT PRIMARY KEY,
    cliente    TEXT NOT NULL,
    intencion  TEXT NOT NULL,
    pregunta   TEXT NOT NULL,
    motivo     TEXT NOT NULL,
    -- Cómo quiere que le contacten. Vacío mientras no lo diga: no se inventa
    -- un canal a partir del correo con el que entró.
    contacto   TEXT NOT NULL DEFAULT '',
    nota       TEXT NOT NULL DEFAULT '',
    estado     TEXT NOT NULL DEFAULT 'abierto',
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS escalamientos_cliente_idx
    ON escalamientos (lower(cliente), creado_en DESC);
"""


@dataclass(frozen=True)
class Caso:
    folio: str
    intencion: str
    pregunta: str
    motivo: str
    contacto: str
    nota: str
    estado: str
    creado_en: datetime

    def as_dict(self) -> dict:
        return {
            "folio": self.folio,
            "intencion": self.intencion,
            "pregunta": self.pregunta,
            # `motivo` es para el log y para quien atienda el caso, no para el
            # cliente: dice por qué falló, y a veces eso es "la factura no está
            # en la cuenta del cliente". No sale por la API.
            "contacto": self.contacto,
            "nota": self.nota,
            "estado": self.estado,
            "creado_en": self.creado_en.isoformat(),
        }


class Escalamientos:
    """Casos escalados en Postgres, acotados por cliente."""

    def __init__(self, conectar) -> None:
        self._conectar = conectar

    def crear_esquema(self) -> None:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(ESQUEMA)
            conn.commit()

    def registrar(
        self, folio: str, cliente: str, *, intencion: str, pregunta: str, motivo: str
    ) -> None:
        """Guarda el caso. Repetir el mismo folio no lo duplica ni lo pisa.

        El folio se deriva del caso, así que la misma consulta el mismo día
        vuelve a dar el mismo. `DO NOTHING` es lo correcto: es el mismo caso otra
        vez, y machacarlo borraría el contacto que el cliente ya hubiera dejado.
        """
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO escalamientos (folio, cliente, intencion, pregunta, motivo)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (folio) DO NOTHING
                """,
                (folio, cliente.strip().lower(), intencion, pregunta[:4000], motivo),
            )
            conn.commit()

    def detallar(self, folio: str, cliente: str) -> Caso | None:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT folio, intencion, pregunta, motivo, contacto, nota, estado, creado_en
                  FROM escalamientos
                 WHERE folio = %s AND lower(cliente) = lower(%s)
                """,
                (folio.strip(), cliente.strip()),
            )
            fila = cur.fetchone()
            return Caso(*fila) if fila else None

    def listar(self, cliente: str, limite: int = 20) -> list[Caso]:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT folio, intencion, pregunta, motivo, contacto, nota, estado, creado_en
                  FROM escalamientos
                 WHERE lower(cliente) = lower(%s)
                 ORDER BY creado_en DESC
                 LIMIT %s
                """,
                (cliente.strip(), limite),
            )
            return [Caso(*fila) for fila in cur.fetchall()]

    def anotar_contacto(self, folio: str, cliente: str, *, contacto: str, nota: str) -> bool:
        """El cliente dice cómo localizarle. Devuelve si el caso era suyo.

        El `WHERE` lleva el cliente: sin él, conocer un folio ajeno —son cortos y
        derivados, no secretos— bastaría para escribir en el caso de otro.
        """
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE escalamientos
                   SET contacto = %s, nota = %s
                 WHERE folio = %s AND lower(cliente) = lower(%s)
                """,
                (contacto.strip()[:MAX_CONTACTO], nota.strip()[:MAX_NOTA],
                 folio.strip(), cliente.strip()),
            )
            cambiadas = cur.rowcount
            conn.commit()
        if not cambiadas:
            logger.warning("Intento de anotar un escalamiento ajeno folio=%s", folio[:32])
        return bool(cambiadas)
