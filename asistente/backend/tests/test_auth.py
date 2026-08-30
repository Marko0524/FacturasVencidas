"""Quién es el que pregunta.

El predicado de permisos de `store.py` solo vale lo que valga la identidad que
se le pasa, así que aquí se verifica que esa identidad no se pueda inventar.
"""

from __future__ import annotations

import pytest

from app.auth import (
    AuthError,
    Identity,
    identity_from_claims,
    parse_links,
    resolve_customer,
    verify_google_token,
)
from tests.conftest import AURORA, LOGISTICA, MERIDIANO

CONOCIDOS = {LOGISTICA, MERIDIANO, AURORA}


def claims(**extra) -> dict:
    base = {
        "iss": "https://accounts.google.com",
        "email": "persona@gmail.com",
        "email_verified": True,
        "name": "Una Persona",
        "picture": "https://ejemplo/foto.jpg",
    }
    base.update(extra)
    return base


# --- enlace entre cuenta de Google y cuenta de cliente -----------------------


def test_an_address_that_is_a_customer_maps_to_itself():
    assert resolve_customer(LOGISTICA, {}, CONOCIDOS) == LOGISTICA


def test_the_mapping_is_case_insensitive():
    assert resolve_customer(LOGISTICA.upper(), {}, CONOCIDOS) == LOGISTICA


def test_a_linked_account_reaches_its_customer():
    enlaces = {"persona@gmail.com": LOGISTICA}

    assert resolve_customer("persona@gmail.com", enlaces, CONOCIDOS) == LOGISTICA


def test_an_unlinked_account_authorises_nothing():
    """Autenticado no es lo mismo que autorizado."""
    assert resolve_customer("desconocido@gmail.com", {}, CONOCIDOS) == ""


def test_a_link_to_a_customer_that_does_not_exist_authorises_nothing():
    enlaces = {"persona@gmail.com": "inventado@ninguna-parte.mx"}

    assert resolve_customer("persona@gmail.com", enlaces, CONOCIDOS) == ""


def test_the_domain_is_never_used_to_guess():
    """Adivinar por dominio es como un cliente acaba leyendo los datos de otro."""
    otro = "cualquiera@meridiano.mx"

    assert resolve_customer(otro, {}, CONOCIDOS) == ""


def test_links_are_parsed_from_one_variable():
    enlaces = parse_links(f" a@gmail.com={LOGISTICA} , B@Gmail.com={MERIDIANO} ")

    assert enlaces == {"a@gmail.com": LOGISTICA, "b@gmail.com": MERIDIANO}


@pytest.mark.parametrize("crudo", ["", "  ", "sin-signo-igual", "=", "a=", "=b"])
def test_malformed_links_are_ignored_not_fatal(crudo: str):
    assert parse_links(crudo) == {}


# --- verificación del token --------------------------------------------------


def test_a_missing_token_is_refused():
    with pytest.raises(AuthError, match="no token"):
        verify_google_token("", "cliente.apps.googleusercontent.com")


def test_a_token_is_refused_when_no_client_id_is_configured():
    """Sin audiencia no hay nada contra qué validar: aceptaría cualquier token."""
    with pytest.raises(AuthError, match="GOOGLE_CLIENT_ID"):
        verify_google_token("loquesea", "")


def test_a_forged_token_is_refused(monkeypatch):
    """Un JWT escrito a mano no sobrevive a la comprobación de firma."""
    def falla(*_args, **_kwargs):
        raise ValueError("Token has wrong signature")

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", falla)

    with pytest.raises(AuthError, match="invalid token"):
        verify_google_token("falsificado", "cliente.apps.googleusercontent.com")


def test_the_reason_a_token_failed_is_not_returned_to_the_caller(monkeypatch):
    """Decirle a quien lo intenta *por qué* falló le ayuda a afinar el intento."""
    def falla(*_args, **_kwargs):
        raise ValueError("Audience mismatch: expected X got Y")

    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", falla)

    with pytest.raises(AuthError) as excinfo:
        verify_google_token("t", "cliente.apps.googleusercontent.com")

    assert "Audience" not in str(excinfo.value)


def test_an_unverified_email_is_refused(monkeypatch):
    """Google permite cuentas con un correo que nunca se demostró."""
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: claims(email_verified=False),
    )

    with pytest.raises(AuthError, match="unverified"):
        verify_google_token("t", "cliente.apps.googleusercontent.com")


def test_a_token_from_another_issuer_is_refused(monkeypatch):
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: claims(iss="https://evil.example.com"),
    )

    with pytest.raises(AuthError, match="issuer"):
        verify_google_token("t", "cliente.apps.googleusercontent.com")


def test_a_token_without_an_email_is_refused(monkeypatch):
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token", lambda *a, **k: claims(email="")
    )

    with pytest.raises(AuthError, match="no email"):
        verify_google_token("t", "cliente.apps.googleusercontent.com")


def test_a_valid_token_yields_its_claims(monkeypatch):
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", lambda *a, **k: claims())

    assert verify_google_token("t", "cliente.apps.googleusercontent.com")["email"] == (
        "persona@gmail.com"
    )


# --- identidad ---------------------------------------------------------------


def test_the_identity_carries_the_linked_customer():
    identidad = identity_from_claims(claims(), {"persona@gmail.com": LOGISTICA}, CONOCIDOS)

    assert identidad.email == "persona@gmail.com"
    assert identidad.customer == LOGISTICA
    assert identidad.linked


def test_an_unlinked_identity_is_authenticated_but_not_authorised():
    identidad = identity_from_claims(claims(), {}, CONOCIDOS)

    assert identidad.email == "persona@gmail.com"
    assert not identidad.linked


def test_the_email_is_normalised():
    identidad = identity_from_claims(claims(email="Persona@GMAIL.com"), {}, CONOCIDOS)

    assert identidad.email == "persona@gmail.com"


def test_a_missing_name_falls_back_to_the_local_part():
    identidad = identity_from_claims(claims(name=None), {}, CONOCIDOS)

    assert identidad.name == "persona"


def test_an_identity_without_a_customer_is_not_linked():
    assert not Identity("a@b.mx", "A", "", "").linked
