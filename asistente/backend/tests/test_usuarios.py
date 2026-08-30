"""Contraseñas y sesiones.

Las de hashing son puras. Las del repositorio necesitan Postgres y se saltan,
diciéndolo, cuando no está: falsear la tabla probaría el falso, y lo que importa
aquí es que la base guarde lo que decimos que guarda.
"""

from __future__ import annotations

import pytest

from app.auth import AuthError, issue_session, read_session
from app.usuarios import (
    RepositorioUsuarios,
    hash_contrasena,
    verificar_contrasena,
)
from tests.test_store import DSN, hay_postgres

SECRETO = "secreto-de-prueba"


# --- hashing -----------------------------------------------------------------


def test_a_correct_password_verifies():
    assert verificar_contrasena("correcta", hash_contrasena("correcta"))


def test_a_wrong_password_does_not():
    assert not verificar_contrasena("otra", hash_contrasena("correcta"))


def test_the_password_is_not_recoverable_from_the_hash():
    """Lo guardado no contiene la contraseña, ni siquiera codificada."""
    guardado = hash_contrasena("secreto-larguisimo")

    assert "secreto-larguisimo" not in guardado
    assert guardado.startswith("scrypt$")


def test_the_same_password_hashes_differently_every_time():
    """La sal por usuario: dos filas iguales no pueden delatarse entre sí."""
    uno = hash_contrasena("misma")
    otro = hash_contrasena("misma")

    assert uno != otro
    assert verificar_contrasena("misma", uno)
    assert verificar_contrasena("misma", otro)


@pytest.mark.parametrize(
    "guardado",
    ["", "sin-formato", "scrypt$solo-una-parte", "bcrypt$aa$bb", "scrypt$zz$zz"],
)
def test_a_malformed_hash_never_verifies(guardado: str):
    """Un registro corrupto no puede convertirse en una puerta abierta."""
    assert not verificar_contrasena("loquesea", guardado)


def test_an_empty_password_still_hashes_and_verifies():
    assert verificar_contrasena("", hash_contrasena(""))


# --- sesiones ----------------------------------------------------------------


def test_a_session_round_trips():
    assert read_session(issue_session("a@b.mx", SECRETO), SECRETO) == "a@b.mx"


def test_the_email_is_normalised_into_the_token():
    assert read_session(issue_session("A@B.MX", SECRETO), SECRETO) == "a@b.mx"


def test_a_tampered_signature_is_refused():
    token = issue_session("a@b.mx", SECRETO)
    alterado = token[:-1] + ("0" if token[-1] != "0" else "1")

    with pytest.raises(AuthError):
        read_session(alterado, SECRETO)


def test_changing_the_email_invalidates_the_token():
    """Sin firma, cambiar de identidad sería escribir texto."""
    token = issue_session("a@b.mx", SECRETO)
    suplantado = "otro@b.mx" + token[token.index(".") :]

    with pytest.raises(AuthError):
        read_session(suplantado, SECRETO)


def test_extending_the_expiry_invalidates_the_token():
    """La expiración va dentro de lo firmado, no al lado."""
    correo, expira, firma = issue_session("a@b.mx", SECRETO).rsplit(".", 2)

    with pytest.raises(AuthError):
        read_session(f"{correo}.{int(expira) + 99999}.{firma}", SECRETO)


def test_another_secret_cannot_read_the_token():
    with pytest.raises(AuthError):
        read_session(issue_session("a@b.mx", SECRETO), "otro-secreto")


def test_an_expired_session_is_refused():
    with pytest.raises(AuthError, match="expired"):
        read_session(issue_session("a@b.mx", SECRETO, ttl=-1), SECRETO)


@pytest.mark.parametrize("token", ["", "sin-puntos", "solo.dos", "a.b.c.d.e"])
def test_a_malformed_token_is_refused(token: str):
    with pytest.raises(AuthError):
        read_session(token, SECRETO)


def test_issuing_without_a_secret_is_refused():
    """Un token sin firmar es un token que cualquiera puede escribir."""
    with pytest.raises(AuthError, match="secret"):
        issue_session("a@b.mx", "")


# --- repositorio -------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not hay_postgres(),
    reason="necesita Postgres: cd asistente && docker compose up -d",
)


@pytestmark_db
class TestRepositorio:
    @pytest.fixture
    def repo(self):
        import psycopg

        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS usuarios CASCADE")
            conn.commit()

        def conectar():
            return psycopg.connect(DSN)

        repositorio = RepositorioUsuarios(conectar)
        repositorio.crear_esquema()
        repositorio.alta("ana@empresa.mx", "Ana", "cliente@empresa.mx", hash_contrasena("clave"))
        return repositorio

    def test_authenticating_with_the_right_password(self, repo):
        usuario = repo.autenticar("ana@empresa.mx", "clave")

        assert usuario is not None
        assert usuario.cliente == "cliente@empresa.mx"

    def test_the_email_is_case_insensitive(self, repo):
        assert repo.autenticar("ANA@Empresa.MX", "clave") is not None

    def test_a_wrong_password_returns_nothing(self, repo):
        assert repo.autenticar("ana@empresa.mx", "otra") is None

    def test_an_unknown_email_returns_nothing(self, repo):
        assert repo.autenticar("nadie@empresa.mx", "clave") is None

    def test_a_deactivated_account_cannot_authenticate(self, repo):
        import psycopg

        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("UPDATE usuarios SET activo = FALSE WHERE correo = 'ana@empresa.mx'")
            conn.commit()

        assert repo.autenticar("ana@empresa.mx", "clave") is None
        # Y tampoco se resuelve desde una sesión ya emitida: los permisos se
        # releen en cada petición, no se congelan en el token.
        assert repo.buscar("ana@empresa.mx") is None

    def test_seeding_twice_does_not_duplicate(self, repo):
        repo.alta("ana@empresa.mx", "Ana", "cliente@empresa.mx", hash_contrasena("otra"))

        assert repo.contar() == 1
        assert repo.autenticar("ana@empresa.mx", "otra") is not None

    def test_a_user_without_a_customer_authenticates_but_authorises_nothing(self, repo):
        repo.alta("libre@empresa.mx", "Libre", "", hash_contrasena("clave"))

        usuario = repo.autenticar("libre@empresa.mx", "clave")

        assert usuario is not None
        assert usuario.cliente == ""
