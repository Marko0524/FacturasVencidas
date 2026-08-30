"""Who is asking.

Everything else in this assistant depends on getting this right: the permission
predicate in `store.py` is only as strong as the identity fed into it. Until now
that identity came from a header the browser could set to anything, which is
fine for a demo and indefensible anywhere else.

Tres modos, y la diferencia entre ellos es el asunto entero:

* ``demo``   — ``X-Demo-Customer`` nombra a un cliente. Cómodo para las pruebas
  y trivialmente falsificable: cualquiera puede mandar cualquier cabecera.
* ``local``  — correo y contraseña contra la tabla ``usuarios`` de Postgres. La
  sesión es un token firmado con HMAC por el servidor.
* ``google`` — un token de identidad firmado por Google, **verificado en el
  servidor**: firma, audiencia, emisor y expiración.

Verifying in the browser would be theatre. A client that decides its own
identity is a client that can decide someone else's, so the token is checked
here, on every request, against Google's published keys.

Authentication and authorisation stay separate, which is not pedantry:
*who you are* (a verified Google account) is not *which account you may read*
(a customer of the insurer). The link between the two is explicit and
configurable, and an authenticated stranger gets exactly the public documents
and nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

# Ocho horas: una jornada. Más largo convierte un token filtrado en un problema
# de días; más corto obliga a volver a entrar en mitad del trabajo.
SESSION_TTL_SECONDS = 8 * 3600


class AuthError(Exception):
    """The caller could not be identified. Never leaks why, to the caller."""


@dataclass(frozen=True)
class Identity:
    """A verified caller, and the customer account they may read."""

    email: str
    name: str
    picture: str
    customer: str

    @property
    def linked(self) -> bool:
        """False when the person is authenticated but owns no customer account."""
        return bool(self.customer)

    def as_dict(self) -> dict:
        return {
            "correo": self.email,
            "nombre": self.name,
            "foto": self.picture,
            "cliente": self.customer,
            "vinculado": self.linked,
        }


def parse_links(raw: str) -> dict[str, str]:
    """Parse ``correo=cliente,correo=cliente`` into a mapping.

    This is the join between a Google account and a customer of the insurer.
    In production it is a table in the system of record; here it is one variable
    so the demo can be driven with a real personal account.
    """
    enlaces: dict[str, str] = {}
    for pieza in raw.split(","):
        pieza = pieza.strip()
        if not pieza or "=" not in pieza:
            continue
        correo, _, cliente = pieza.partition("=")
        correo, cliente = correo.strip().lower(), cliente.strip().lower()
        if correo and cliente:
            enlaces[correo] = cliente
    return enlaces


def resolve_customer(email: str, links: dict[str, str], known: set[str]) -> str:
    """Which customer account this verified email may read.

    An address that *is* a customer maps to itself; anything else needs an
    explicit link. Guessing — by domain, say — is how one customer ends up
    reading another's documents.
    """
    correo = email.strip().lower()
    if correo in known:
        return correo

    enlazado = links.get(correo, "")
    if enlazado and enlazado in known:
        return enlazado
    if enlazado:
        logger.warning("Account link points at an unknown customer: %s", enlazado)
    return ""


def verify_google_token(token: str, client_id: str) -> dict:
    """Verify a Google ID token and return its claims.

    ``verify_oauth2_token`` checks the signature against Google's published
    keys, the audience, the issuer and the expiry. Decoding the JWT without
    verifying it — which is one convenient function call away — would accept a
    token anybody could have written by hand.
    """
    if not token:
        raise AuthError("no token")
    if not client_id:
        raise AuthError("GOOGLE_CLIENT_ID is not configured")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise AuthError("google-auth is required for Google sign-in") from exc

    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
    except Exception as exc:  # noqa: BLE001 - google-auth raises broadly
        # The reason is logged, never returned: telling a caller *why* their
        # token failed helps them craft a better one.
        logger.info("Rejected Google token: %s", exc)
        raise AuthError("invalid token") from exc

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("invalid issuer")

    # An unverified address is a claim, not a fact: Google lets an account
    # exist with an email it never proved. Treating it as identity would let
    # anyone claim any address.
    if not claims.get("email_verified"):
        raise AuthError("unverified email")
    if not claims.get("email"):
        raise AuthError("token carries no email")

    return claims


def identity_from_claims(claims: dict, links: dict[str, str], known: set[str]) -> Identity:
    email = str(claims["email"]).strip().lower()
    return Identity(
        email=email,
        name=str(claims.get("name") or email.split("@")[0]),
        picture=str(claims.get("picture") or ""),
        customer=resolve_customer(email, links, known),
    )


# --- sesión local -------------------------------------------------------------


def issue_session(email: str, secret: str, *, now: int | None = None,
                  ttl: int = SESSION_TTL_SECONDS) -> str:
    """Emite un token de sesión firmado: ``<correo>.<expira>.<firma>``.

    Firmado y no cifrado: su contenido no es secreto, lo que importa es que no
    se pueda modificar. Sin firma, cambiar el correo del token sería cambiar de
    identidad escribiendo texto.

    La expiración va *dentro* de lo firmado. Si viajara aparte, alargarla sería
    tan fácil como editarla.
    """
    if not secret:
        raise AuthError("no session secret configured")
    expira = (now if now is not None else int(time.time())) + ttl
    cuerpo = f"{email.strip().lower()}.{expira}"
    return f"{cuerpo}.{_firmar(cuerpo, secret)}"


def read_session(token: str, secret: str, *, now: int | None = None) -> str:
    """Devuelve el correo de un token válido, o lanza ``AuthError``."""
    if not token or not secret:
        raise AuthError("invalid session")

    partes = token.rsplit(".", 2)
    if len(partes) != 3:
        raise AuthError("invalid session")

    email, expira_txt, firma = partes
    cuerpo = f"{email}.{expira_txt}"

    # Se comprueba la firma ANTES de mirar nada más: hasta que se verifica, el
    # token es texto que mandó un desconocido.
    if not hmac.compare_digest(firma, _firmar(cuerpo, secret)):
        raise AuthError("invalid session")

    try:
        expira = int(expira_txt)
    except ValueError as exc:
        raise AuthError("invalid session") from exc

    if (now if now is not None else int(time.time())) >= expira:
        raise AuthError("session expired")
    return email


def _firmar(cuerpo: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), cuerpo.encode("utf-8"), hashlib.sha256).hexdigest()
