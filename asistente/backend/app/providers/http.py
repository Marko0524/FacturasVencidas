"""Shared HTTP behaviour for the two real providers.

Same policy as the reminder job's ``api_client``: retry what is worth retrying,
fail fast on what is not. A hosted model is a shared resource — 429 and 503 are
normal weather, not exceptions — while a 401 or a bad deployment name will
answer identically however many times it is asked.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time

import requests

from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

# 408 request timeout, 429 rate limited, 5xx upstream trouble. Everything else
# is a client error: retrying it just burns quota and delays the escalation.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# A rate limit that says "come back in an hour" is not a spike to ride out. It
# is a daily quota, and retrying it burns what little is left while the customer
# waits for an escalation that was already inevitable.
MAX_RETRY_AFTER = 60.0

DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


def post_json(
    session: requests.Session,
    url: str,
    *,
    headers: dict,
    payload: dict,
    timeout: float,
    provider: str,
    max_retries: int = 3,
    backoff_base: float = 1.0,
) -> dict:
    """POST with selective retries and full-jitter backoff.

    Returns the decoded body, or raises ``ProviderError`` — the assistant's
    signal to escalate to a human rather than to guess.
    """
    last_error = ""

    for attempt in range(max_retries + 1):
        delay: float | None = None

        try:
            response = session.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.Timeout as exc:
            if attempt == max_retries:
                raise ProviderError(f"{provider} unreachable: {exc}") from exc
            last_error = f"timeout after {timeout}s"
        except requests.ConnectionError as exc:
            # Sí merece reintento, al contrario de lo que decía este código.
            #
            # El caso real: la sesión mantiene la conexión abierta entre
            # peticiones, el servidor la cierra por inactividad, y la siguiente
            # petición se encuentra el extremo cerrado —"RemoteDisconnected"—
            # antes de enviar nada. Reintentar abre una conexión nueva y
            # funciona a la primera. Es el fallo transitorio más común con una
            # sesión persistente, y sin esto le costaba al cliente un
            # escalamiento con folio por un parpadeo de red.
            #
            # Un DNS que no resuelve tampoco se arregla en tres segundos, cierto,
            # pero reintentarlo cuesta unos segundos de espera; no reintentar lo
            # otro cuesta una respuesta perdida. El reintento es seguro: si la
            # conexión se cerró antes de enviar, no se procesó nada.
            if attempt == max_retries:
                raise ProviderError(f"{provider} unreachable: {exc}") from exc
            last_error = f"conexión perdida: {type(exc).__name__}"
        except requests.RequestException as exc:
            # Lo demás —una URL inválida, un esquema que no existe— no cambia
            # por insistir.
            raise ProviderError(f"{provider} unreachable: {exc}") from exc
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError(f"{provider} returned a body that is not JSON") from exc

            body = response.text[:400]
            if response.status_code not in RETRYABLE_STATUS:
                raise ProviderError(
                    f"{provider} returned status={response.status_code} body={body}"
                )

            last_error = f"status={response.status_code} body={body}"
            if attempt == max_retries:
                raise ProviderError(f"{provider} kept failing: {last_error}")

            # The server usually knows better than the backoff formula how long
            # it needs. If what it asks for is longer than we are willing to
            # hold a request open, stop instead of pretending to wait.
            requested = retry_after(response)
            if requested is not None:
                if requested > MAX_RETRY_AFTER:
                    raise ProviderError(
                        f"{provider} is rate limited for {requested:.0f}s, "
                        f"longer than this request will wait: {body}"
                    )
                delay = requested

        if delay is None:
            delay = _backoff(attempt, backoff_base)

        logger.warning(
            "%s retrying attempt=%d/%d in %.1fs reason=%s",
            provider, attempt + 1, max_retries, delay, last_error[:160],
        )
        time.sleep(delay)

    raise ProviderError(f"{provider} kept failing: {last_error}")  # pragma: no cover


def retry_after(response: requests.Response) -> float | None:
    """How long the server asked us to wait, in seconds, if it said so.

    Two dialects: the standard ``Retry-After`` header, and the ``retryDelay``
    that Google buries in the error details as ``"27s"``.
    """
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header.strip())
        except ValueError:
            pass  # The HTTP-date form exists; the backoff covers that case.

    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    if not isinstance(details, list):
        return None

    for detail in details:
        if not isinstance(detail, dict):
            continue
        match = DURATION.match(str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


def _backoff(attempt: int, base: float) -> float:
    """Exponential backoff with full jitter.

    The jitter is not decoration: without it, every client that got the same
    503 comes back at the same instant and recreates the spike.
    """
    return random.uniform(base * (2**attempt), 2 * base * (2**attempt))
