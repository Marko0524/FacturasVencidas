"""Usuarios en la base de datos.

Hasta ahora la identidad venía de una cabecera que el navegador podía escribir a
su gusto. Aquí pasa a ser una fila en Postgres con una contraseña verificable.

Dos cosas que este módulo no hace, y son las importantes:

* **No guarda contraseñas.** Guarda el resultado de pasarlas por ``scrypt`` con
  una sal distinta por usuario. Una fuga de la tabla no entrega las
  contraseñas, y dos personas con la misma contraseña no comparten hash.
* **No dice qué falló.** Correo inexistente y contraseña incorrecta devuelven lo
  mismo. Distinguirlos convierte el formulario en un buscador de correos
  válidos, que es el primer paso de cualquier ataque dirigido.

Se elige ``scrypt`` porque está en la biblioteca estándar y es *memory-hard*:
una GPU no le saca la ventaja enorme que le saca a un SHA a secas. Los
parámetros son los recomendados para un inicio de sesión interactivo.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# n=2^14, r=8, p=1: coste interactivo estándar, alrededor de 100 ms por intento.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 32
SALT_LEN = 16

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          BIGSERIAL PRIMARY KEY,
    correo      TEXT NOT NULL UNIQUE,
    nombre      TEXT NOT NULL,
    -- La cuenta de cliente que este usuario puede leer. Autenticarse y estar
    -- autorizado siguen siendo cosas distintas: la columna puede ir vacía.
    cliente     TEXT NOT NULL DEFAULT '',
    contrasena  TEXT NOT NULL,
    activo      BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS usuarios_correo_idx ON usuarios (lower(correo));
"""


@dataclass(frozen=True)
class Usuario:
    correo: str
    nombre: str
    cliente: str


def hash_contrasena(contrasena: str) -> str:
    """``scrypt$<sal hex>$<hash hex>``. La sal es nueva en cada llamada."""
    sal = secrets.token_bytes(SALT_LEN)
    derivada = hashlib.scrypt(
        contrasena.encode("utf-8"), salt=sal, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN
    )
    return f"scrypt${sal.hex()}${derivada.hex()}"


def verificar_contrasena(contrasena: str, guardado: str) -> bool:
    """Comprueba una contraseña contra su hash, en tiempo constante."""
    try:
        algoritmo, sal_hex, esperado_hex = guardado.split("$", 2)
    except ValueError:
        return False
    if algoritmo != "scrypt":
        return False

    try:
        sal = bytes.fromhex(sal_hex)
        esperado = bytes.fromhex(esperado_hex)
    except ValueError:
        return False

    derivada = hashlib.scrypt(
        contrasena.encode("utf-8"), salt=sal, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN
    )
    # `compare_digest` y no `==`: comparar byte a byte con salida temprana
    # filtra, por el tiempo que tarda, cuántos caracteres se acertaron.
    return hmac.compare_digest(derivada, esperado)


class RepositorioUsuarios:
    """Lectura y escritura de la tabla ``usuarios``."""

    def __init__(self, conectar) -> None:
        # Recibe la función de conexión en vez de abrirla: así comparte la
        # misma configuración que el almacén vectorial y no hay dos verdades
        # sobre a qué base apuntamos.
        self._conectar = conectar

    def crear_esquema(self) -> None:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(ESQUEMA)
            conn.commit()

    def alta(self, correo: str, nombre: str, cliente: str, contrasena: str) -> None:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usuarios (correo, nombre, cliente, contrasena)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (correo) DO UPDATE
                    SET nombre = EXCLUDED.nombre,
                        cliente = EXCLUDED.cliente,
                        contrasena = EXCLUDED.contrasena
                """,
                (correo.strip().lower(), nombre.strip(), cliente.strip().lower(), contrasena),
            )
            conn.commit()

    def contar(self) -> int:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM usuarios")
            return cur.fetchone()[0]

    def buscar(self, correo: str) -> Usuario | None:
        """El usuario por su correo, sin comprobar contraseña.

        Lo usa cada petición ya autenticada: el token dice quién entró, pero los
        permisos se releen aquí para que desactivar una cuenta surta efecto sin
        esperar a que caduque la sesión.
        """
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT correo, nombre, cliente FROM usuarios "
                "WHERE lower(correo) = lower(%s) AND activo",
                (correo.strip(),),
            )
            fila = cur.fetchone()
        return Usuario(*fila) if fila else None

    def autenticar(self, correo: str, contrasena: str) -> Usuario | None:
        """Devuelve el usuario si las credenciales son correctas, o ``None``.

        Un solo ``None`` para todos los casos —correo desconocido, contraseña
        incorrecta, cuenta desactivada— porque quien lo llama no debe poder
        distinguirlos ni siquiera por accidente.
        """
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT correo, nombre, cliente, contrasena, activo "
                "FROM usuarios WHERE lower(correo) = lower(%s)",
                (correo.strip(),),
            )
            fila = cur.fetchone()

        if fila is None:
            # Se gasta el mismo trabajo que en un intento real. Sin esto, un
            # correo inexistente responde mucho antes que uno existente y el
            # propio tiempo revela qué cuentas hay.
            verificar_contrasena(contrasena, _HASH_SENUELO)
            return None

        correo_bd, nombre, cliente, guardado, activo = fila
        if not verificar_contrasena(contrasena, guardado):
            return None
        if not activo:
            return None
        return Usuario(correo=correo_bd, nombre=nombre, cliente=cliente)


# Hash de una contraseña que nadie conoce, para gastar el mismo tiempo cuando el
# correo no existe.
_HASH_SENUELO = hash_contrasena(secrets.token_urlsafe(32))


def contrasena_semilla() -> str:
    """La contraseña con la que se siembran los usuarios de demostración."""
    return os.getenv("SEED_PASSWORD", "").strip() or "asistente2026"
