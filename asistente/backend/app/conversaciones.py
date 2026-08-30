"""Memoria de conversación.

Sirve para que "resúmelo" o "¿y el deducible?" tengan a qué referirse. Hasta
ahora cada pregunta se resolvía sola, y un pronombre sin antecedente no se puede
responder: solo escalar.

**La memoria vive en el servidor, no en el navegador.** La alternativa —que el
cliente mande el historial en cada petición— es más simple y está mal: un
historial que escribe el cliente es un historial que se puede inventar, y ese
texto acaba dentro del prompt. Aquí el cliente manda un identificador de
conversación y nada más; qué se dijo lo sabe la base.

Cada conversación pertenece a una cuenta de cliente y se comprueba en cada
lectura. Un identificador adivinado no abre la conversación de otro.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Cuántos turnos se recuerdan. Suficiente para resolver una referencia a lo
# anterior sin arrastrar media conversación a cada prompt, que cuesta dinero y
# diluye la pregunta actual.
TURNOS_RECORDADOS = 6

# Cuánto de la primera pregunta se guarda como título de la conversación. Un
# título es para reconocerla en una lista, no para leerla entera.
MAX_TITULO = 80

ESQUEMA = """
CREATE TABLE IF NOT EXISTS conversaciones (
    id         UUID PRIMARY KEY,
    cliente    TEXT NOT NULL,
    creada_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversaciones_cliente_idx ON conversaciones (lower(cliente));

CREATE TABLE IF NOT EXISTS turnos (
    id              BIGSERIAL PRIMARY KEY,
    conversacion_id UUID NOT NULL REFERENCES conversaciones (id) ON DELETE CASCADE,
    orden           INT NOT NULL,
    rol             TEXT NOT NULL CHECK (rol IN ('cliente', 'asistente')),
    texto           TEXT NOT NULL,
    -- El documento que citó la respuesta, si citó alguno. Es lo que convierte
    -- "resúmelo" en una referencia resoluble.
    documento       TEXT NOT NULL DEFAULT '',
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversacion_id, orden)
);

-- Añadidas después de la primera versión. `IF NOT EXISTS` para que una base ya
-- creada se ponga al día al arrancar en vez de exigir una migración a mano.
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS titulo TEXT NOT NULL DEFAULT '';
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS ultima_en TIMESTAMPTZ NOT NULL DEFAULT now();

-- La valoración de una respuesta: +1 útil, -1 no. NULL es "no se ha dicho", que
-- es distinto de cero y por eso no se usa un entero con defecto.
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS valoracion SMALLINT;
ALTER TABLE turnos ADD COLUMN IF NOT EXISTS comentario TEXT NOT NULL DEFAULT '';

-- De qué documento se está hablando ahora mismo.
--
-- Antes esto se deducía recorriendo los turnos hacia atrás en busca del último
-- que hubiera citado algo. Eso solo ve lo que el asistente respondió, y subir
-- un documento no es una respuesta: quien subía un archivo y preguntaba "¿de
-- qué trata?" recibía el resumen del documento ANTERIOR, porque era el único
-- que constaba. Guardarlo en la conversación permite que cualquier cosa que
-- cambie el asunto lo actualice: una cita, o una carga.
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS documento_actual TEXT NOT NULL DEFAULT '';
"""


@dataclass(frozen=True)
class Turno:
    rol: str
    texto: str
    documento: str


class Conversaciones:
    """Turnos de conversación en Postgres, acotados por cliente."""

    def __init__(self, conectar) -> None:
        self._conectar = conectar

    def crear_esquema(self) -> None:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(ESQUEMA)
            conn.commit()

    def abrir(self, cliente: str) -> str:
        """Empieza una conversación y devuelve su identificador."""
        identificador = str(uuid.uuid4())
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversaciones (id, cliente) VALUES (%s, %s)",
                (identificador, cliente.strip().lower()),
            )
            conn.commit()
        return identificador

    def pertenece(self, conversacion: str, cliente: str) -> bool:
        """Si esta conversación es de este cliente.

        Se comprueba en cada lectura y en cada escritura. Un identificador es
        adivinable —son UUID, pero aun así— y sin esta comprobación adivinar uno
        abriría la conversación de otra cuenta.
        """
        if not _es_uuid(conversacion):
            return False
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM conversaciones WHERE id = %s AND lower(cliente) = lower(%s)",
                (conversacion, cliente.strip()),
            )
            return cur.fetchone() is not None

    def recordar(self, conversacion: str, cliente: str) -> list[Turno]:
        """Los últimos turnos, en orden, si la conversación es de este cliente."""
        if not self.pertenece(conversacion, cliente):
            return []
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT rol, texto, documento FROM (
                    SELECT rol, texto, documento, orden
                      FROM turnos
                     WHERE conversacion_id = %s
                     ORDER BY orden DESC
                     LIMIT %s
                ) ultimos
                ORDER BY orden ASC
                """,
                (conversacion, TURNOS_RECORDADOS),
            )
            return [Turno(*fila) for fila in cur.fetchall()]

    def anotar(
        self,
        conversacion: str,
        cliente: str,
        *,
        pregunta: str,
        respuesta: str,
        documento: str = "",
    ) -> int:
        """Guarda el par pregunta/respuesta y devuelve el id del turno del asistente.

        Ese id es lo que permite valorar *esta* respuesta y no la conversación
        entera: sin él, un "no me sirvió" no diría a cuál de seis respuestas se
        refiere, y el dato serviría de poco.
        """
        if not self.pertenece(conversacion, cliente):
            logger.warning("Intento de escribir en una conversación ajena")
            return 0

        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT coalesce(max(orden), 0) FROM turnos WHERE conversacion_id = %s",
                (conversacion,),
            )
            siguiente = cur.fetchone()[0] + 1
            cur.execute(
                "INSERT INTO turnos (conversacion_id, orden, rol, texto) VALUES (%s, %s, %s, %s)",
                (conversacion, siguiente, "cliente", pregunta),
            )
            cur.execute(
                "INSERT INTO turnos (conversacion_id, orden, rol, texto, documento) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (conversacion, siguiente + 1, "asistente", respuesta, documento),
            )
            turno = cur.fetchone()[0]

            # El título se pone con la primera pregunta y ya no cambia: una lista
            # cuyos nombres se mueven solos no sirve para reconocer nada.
            #
            # El documento en curso solo se toca si esta respuesta citó alguno.
            # Una consulta de facturas no cita ninguno y no por eso deja de
            # hablarse del documento de antes: borrarlo ahí rompería el
            # "resúmelo" de la pregunta siguiente.
            cur.execute(
                """
                UPDATE conversaciones
                   SET ultima_en = now(),
                       titulo = CASE WHEN titulo = '' THEN %s ELSE titulo END,
                       documento_actual = CASE WHEN %s <> '' THEN %s ELSE documento_actual END
                 WHERE id = %s
                """,
                (pregunta.strip()[:MAX_TITULO], documento, documento, conversacion),
            )
            conn.commit()
        return turno

    def valorar(self, turno: int, cliente: str, *, util: bool, comentario: str = "") -> bool:
        """Marca una respuesta como útil o no. Devuelve si el turno era suyo.

        La comprobación de propiedad va dentro del propio UPDATE, unida a
        `conversaciones`: leer primero y escribir después dejaría un hueco, y un
        id de turno es un entero correlativo — el más adivinable que hay.
        """
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE turnos t
                   SET valoracion = %s, comentario = %s
                  FROM conversaciones c
                 WHERE t.id = %s
                   AND t.conversacion_id = c.id
                   AND t.rol = 'asistente'
                   AND lower(c.cliente) = lower(%s)
                """,
                (1 if util else -1, comentario.strip()[:1000], turno, cliente.strip()),
            )
            cambiadas = cur.rowcount
            conn.commit()
        if not cambiadas:
            logger.warning("Intento de valorar un turno ajeno o inexistente turno=%s", turno)
        return bool(cambiadas)

    def listar(self, cliente: str, limite: int = 30) -> list[dict]:
        """Las conversaciones de este cliente, la más reciente primero."""
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.titulo, c.ultima_en, count(t.id) FILTER (WHERE t.rol = 'cliente')
                  FROM conversaciones c
                  LEFT JOIN turnos t ON t.conversacion_id = c.id
                 WHERE lower(c.cliente) = lower(%s)
                 GROUP BY c.id, c.titulo, c.ultima_en
                HAVING count(t.id) > 0
                 ORDER BY c.ultima_en DESC
                 LIMIT %s
                """,
                (cliente.strip(), limite),
            )
            return [
                {
                    "id": str(fila[0]),
                    "titulo": fila[1] or "Consulta sin título",
                    "ultima_en": fila[2].isoformat(),
                    "preguntas": fila[3],
                }
                for fila in cur.fetchall()
            ]

    def transcribir(self, conversacion: str, cliente: str) -> list[dict]:
        """La conversación entera, para volver a abrirla en la interfaz.

        Distinta de ``recordar``: aquella devuelve los últimos turnos para el
        prompt, esta devuelve todos para la pantalla.
        """
        if not self.pertenece(conversacion, cliente):
            return []
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, rol, texto, documento, valoracion
                  FROM turnos
                 WHERE conversacion_id = %s
                 ORDER BY orden ASC
                """,
                (conversacion,),
            )
            return [
                {
                    "turno": fila[0],
                    "rol": fila[1],
                    "texto": fila[2],
                    "documento": fila[3],
                    "valoracion": fila[4],
                }
                for fila in cur.fetchall()
            ]

    def ultimo_documento(self, conversacion: str, cliente: str) -> str:
        """De qué documento se está hablando, para resolver "resúmelo"."""
        if not self.pertenece(conversacion, cliente):
            return ""
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT documento_actual FROM conversaciones WHERE id = %s", (conversacion,)
            )
            fila = cur.fetchone()
            return fila[0] if fila else ""

    def fijar_documento(self, conversacion: str, cliente: str, documento: str) -> bool:
        """Cambia de qué se está hablando. Lo usa la carga de un documento.

        Subir un archivo es decir "hablemos de esto" con más claridad que
        cualquier pronombre, así que manda sobre lo que se citó antes.
        """
        if not documento or not self.pertenece(conversacion, cliente):
            return False
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE conversaciones SET documento_actual = %s WHERE id = %s",
                (documento, conversacion),
            )
            conn.commit()
        return True

    def olvidar(self, conversacion: str, cliente: str) -> bool:
        """Borra una conversación propia. El borrado en cascada se lleva los turnos."""
        if not self.pertenece(conversacion, cliente):
            return False
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM conversaciones WHERE id = %s", (conversacion,))
            conn.commit()
        return True


def formatear(turnos: list[Turno], limite_caracteres: int = 1200) -> str:
    """El historial como texto para el prompt, recortado y delimitado.

    Va etiquetado como transcripción y no como instrucciones: son turnos
    escritos por una persona, y sin la etiqueta un "a partir de ahora eres…"
    de hace tres preguntas se leería como una orden del sistema.
    """
    if not turnos:
        return ""

    lineas = []
    total = 0
    for turno in reversed(turnos):
        quien = "Cliente" if turno.rol == "cliente" else "Asistente"
        linea = f"{quien}: {turno.texto.strip()[:300]}"
        if total + len(linea) > limite_caracteres:
            break
        lineas.append(linea)
        total += len(linea)

    if not lineas:
        return ""
    return "\n".join(reversed(lineas))


def _es_uuid(valor: str) -> bool:
    try:
        uuid.UUID(str(valor))
        return True
    except (ValueError, AttributeError, TypeError):
        return False
