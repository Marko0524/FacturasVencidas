"""Retry policy tests. No network, no real sleeping."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest
import requests

from app.api_client import ApiError, fetch_raw_invoices
from app.config import Config

API_URL = "https://api.example.com/invoices"

INVOICE = {
    "id": "INV-1001",
    "customer_name": "Empresa Demo",
    "customer_email": "cliente@empresa.com",
    "amount": 15000.50,
    "currency": "MXN",
    "due_date": "2026-08-05",
    "status": "pending",
}


class FakeResponse:
    def __init__(self, status_code: int, payload=None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeSession:
    """Returns queued responses, or raises queued exceptions, in order."""

    def __init__(self, outcomes: list):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if not self._outcomes:
            raise AssertionError("the client made more requests than the test queued")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_config(tmp_path: Path, url: str = API_URL, max_retries: int = 3) -> Config:
    return Config(
        invoices_api_url=url,
        api_token="s3cr3t-token",
        operations_email="operaciones@empresa.com",
        request_timeout=7.5,
        max_retries=max_retries,
        retry_backoff_base=0.5,
        overdue_alert_threshold_days=10,
        state_file_path=tmp_path / "notifications.json",
        sample_data_path=tmp_path / "invoices.json",
        log_level="INFO",
        dry_run=False,
        notification_channel="log",
        smtp_host="localhost",
        smtp_port=1025,
        smtp_username="",
        smtp_password="",
        smtp_use_tls=False,
        email_from="cobranza@empresa.com",
        email_from_name="Cobranza Empresa",
    )


def fetch(config, session, sleep):
    return fetch_raw_invoices(config, session=session, sleep=sleep, rng=random.Random(0))


# --- happy path -------------------------------------------------------------


def test_successful_response_returns_the_invoice_records(tmp_path: Path):
    session = FakeSession([FakeResponse(200, {"data": [INVOICE]})])
    sleep = FakeSleep()

    records = fetch(make_config(tmp_path), session, sleep)

    assert records == [INVOICE]
    assert len(session.calls) == 1
    assert sleep.delays == []


def test_token_is_sent_as_a_bearer_header_and_timeout_is_applied(tmp_path: Path):
    session = FakeSession([FakeResponse(200, {"data": []})])

    fetch(make_config(tmp_path), session, FakeSleep())

    assert session.calls[0]["headers"]["Authorization"] == "Bearer s3cr3t-token"
    assert session.calls[0]["timeout"] == 7.5


def test_no_authorization_header_when_no_token(tmp_path: Path):
    config = replace(make_config(tmp_path), api_token="")
    session = FakeSession([FakeResponse(200, {"data": []})])

    fetch(config, session, FakeSleep())

    assert "Authorization" not in session.calls[0]["headers"]


# --- retryable failures -----------------------------------------------------


def test_503_is_retried_until_max_attempts_then_fails(tmp_path: Path):
    session = FakeSession([FakeResponse(503) for _ in range(4)])
    sleep = FakeSleep()

    with pytest.raises(ApiError, match="status=503"):
        fetch(make_config(tmp_path, max_retries=3), session, sleep)

    assert len(session.calls) == 4  # 1 initial attempt + 3 retries
    assert len(sleep.delays) == 3


def test_transient_failure_followed_by_success_returns_data(tmp_path: Path):
    session = FakeSession([FakeResponse(500), FakeResponse(200, {"data": [INVOICE]})])
    sleep = FakeSleep()

    records = fetch(make_config(tmp_path), session, sleep)

    assert records == [INVOICE]
    assert len(session.calls) == 2
    assert len(sleep.delays) == 1


def test_connection_errors_are_retried(tmp_path: Path):
    session = FakeSession(
        [requests.ConnectionError("dns failure"), FakeResponse(200, {"data": [INVOICE]})]
    )
    sleep = FakeSleep()

    assert fetch(make_config(tmp_path), session, sleep) == [INVOICE]
    assert len(session.calls) == 2


def test_timeouts_are_retried_and_eventually_raise(tmp_path: Path):
    session = FakeSession([requests.Timeout("too slow") for _ in range(3)])

    with pytest.raises(ApiError, match="unreachable"):
        fetch(make_config(tmp_path, max_retries=2), session, FakeSleep())

    assert len(session.calls) == 3


def test_retry_after_header_is_honoured_on_429(tmp_path: Path):
    session = FakeSession(
        [FakeResponse(429, headers={"Retry-After": "2"}), FakeResponse(200, {"data": []})]
    )
    sleep = FakeSleep()

    fetch(make_config(tmp_path), session, sleep)

    assert sleep.delays == [2.0]


def test_backoff_grows_between_attempts(tmp_path: Path):
    session = FakeSession([FakeResponse(502) for _ in range(4)])
    sleep = FakeSleep()

    with pytest.raises(ApiError):
        fetch(make_config(tmp_path, max_retries=3), session, sleep)

    # base 0.5 with full jitter: attempt n sleeps within [base*2^(n-1), 2*base*2^(n-1)]
    assert 0.5 <= sleep.delays[0] <= 1.0
    assert 1.0 <= sleep.delays[1] <= 2.0
    assert 2.0 <= sleep.delays[2] <= 4.0


# --- non retryable failures -------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(tmp_path: Path, status: int):
    session = FakeSession([FakeResponse(status)])
    sleep = FakeSleep()

    with pytest.raises(ApiError, match="non-retryable"):
        fetch(make_config(tmp_path), session, sleep)

    assert len(session.calls) == 1
    assert sleep.delays == []


def test_invalid_json_body_raises(tmp_path: Path):
    session = FakeSession([FakeResponse(200, payload=None)])

    with pytest.raises(ApiError, match="not valid JSON"):
        fetch(make_config(tmp_path), session, FakeSleep())


def test_payload_without_a_data_key_raises(tmp_path: Path):
    session = FakeSession([FakeResponse(200, {"invoices": []})])

    with pytest.raises(ApiError, match="'data' key"):
        fetch(make_config(tmp_path), session, FakeSleep())


# --- file source ------------------------------------------------------------


def test_file_source_is_used_when_no_api_url(tmp_path: Path):
    config = make_config(tmp_path, url="")
    config.sample_data_path.write_text(json.dumps({"data": [INVOICE]}), encoding="utf-8")

    assert fetch(config, FakeSession([]), FakeSleep()) == [INVOICE]


def test_missing_sample_file_raises(tmp_path: Path):
    config = make_config(tmp_path, url="")

    with pytest.raises(ApiError, match="not found"):
        fetch(config, FakeSession([]), FakeSleep())
