"""Invoice retrieval.

Two interchangeable sources behind one function:

* the REST API, when ``INVOICES_API_URL`` is set (with timeout, retries and backoff);
* a local JSON file otherwise, so the app runs with no network and no config.

Both sources return the same shape, so the rest of the app cannot tell them apart.
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Callable

import requests

from app.config import Config

logger = logging.getLogger(__name__)

# Transient failures: worth retrying.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Any other 4xx is a client-side problem; retrying it only wastes time.
MAX_BACKOFF_SECONDS = 30.0


class ApiError(RuntimeError):
    """Raised when invoices cannot be retrieved from the configured source."""


def fetch_raw_invoices(
    config: Config,
    session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> list[Any]:
    """Return the raw invoice records from the configured source.

    ``session``, ``sleep`` and ``rng`` are injectable so the retry policy can be
    tested without network calls and without actually waiting.
    """
    if config.uses_api:
        client = InvoiceApiClient(config, session=session, sleep=sleep, rng=rng)
        return client.fetch()
    return _read_invoices_file(config.sample_data_path)


class InvoiceApiClient:
    """Thin HTTP client with an explicit, selective retry policy."""

    def __init__(
        self,
        config: Config,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._session = session if session is not None else requests.Session()
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()

    def fetch(self) -> list[Any]:
        """Fetch and parse the invoice payload, retrying transient failures."""
        response = self._request_with_retries()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError("API response body is not valid JSON") from exc
        return _extract_invoices(payload, "api")

    def _request_with_retries(self) -> Any:
        url = self._config.invoices_api_url
        total_attempts = self._config.max_retries + 1
        logger.info(
            "Fetching invoices from API url=%s timeout=%ss max_attempts=%d",
            url,
            self._config.request_timeout,
            total_attempts,
        )

        for attempt in range(1, total_attempts + 1):
            try:
                response = self._session.get(
                    url,
                    headers=self._headers(),
                    timeout=self._config.request_timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == total_attempts:
                    raise ApiError(
                        f"API unreachable after {total_attempts} attempts: {type(exc).__name__}"
                    ) from exc
                self._wait_before_retry(attempt, total_attempts, f"error={type(exc).__name__}", None)
                continue

            status = response.status_code
            if status in RETRYABLE_STATUS_CODES:
                if attempt == total_attempts:
                    raise ApiError(f"API returned status={status} after {total_attempts} attempts")
                self._wait_before_retry(attempt, total_attempts, f"status={status}", response)
                continue

            if status >= 400:
                raise ApiError(f"API returned non-retryable status={status}")

            return response

        # Defensive: the loop above always returns or raises.
        raise ApiError("API retry loop ended without a response")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._config.api_token:
            headers["Authorization"] = f"Bearer {self._config.api_token}"
        return headers

    def _wait_before_retry(
        self,
        attempt: int,
        total_attempts: int,
        reason: str,
        response: Any | None,
    ) -> None:
        delay = self._retry_after_seconds(response)
        if delay is None:
            backoff = self._config.retry_backoff_base * (2 ** (attempt - 1))
            # Full jitter: spreads retries out so concurrent clients do not sync up.
            delay = min(backoff + self._rng.uniform(0.0, backoff), MAX_BACKOFF_SECONDS)
        logger.warning(
            "Retrying API request attempt=%d/%d %s sleep=%.2fs",
            attempt,
            total_attempts,
            reason,
            delay,
        )
        self._sleep(delay)

    @staticmethod
    def _retry_after_seconds(response: Any | None) -> float | None:
        """Honour ``Retry-After`` on 429 responses (seconds form only)."""
        if response is None or response.status_code != 429:
            return None
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return None  # HTTP-date form is not supported; fall back to backoff.
        return min(max(seconds, 0.0), MAX_BACKOFF_SECONDS)


def _read_invoices_file(path: Path) -> list[Any]:
    logger.info("Reading invoices from local file path=%s", path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApiError(f"Sample data file not found path={path}") from exc
    except (OSError, ValueError) as exc:
        raise ApiError(f"Sample data file is not readable JSON path={path}") from exc
    return _extract_invoices(payload, "file")


def _extract_invoices(payload: Any, origin: str) -> list[Any]:
    """Both sources must return ``{"data": [...]}``."""
    if not isinstance(payload, dict) or "data" not in payload:
        raise ApiError(f"Unexpected payload from {origin}: expected an object with a 'data' key")
    data = payload["data"]
    if not isinstance(data, list):
        raise ApiError(f"Unexpected payload from {origin}: 'data' must be a list")
    return data
