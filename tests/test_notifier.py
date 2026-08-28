"""SMTP delivery tests. No socket is ever opened: ``smtplib.SMTP`` is faked."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import main
from app.config import Config
from app.invoice_service import (
    TYPE_OPERATIONS_ALERT,
    TYPE_REMINDER,
    Invoice,
    Notification,
)
from app.notifier import LoggingNotifier, NotificationError, SmtpNotifier

INVOICE = Invoice(
    id="INV-1007",
    customer_name="Logistica Pacifico",
    customer_email="pagos@logpacifico.mx",
    amount=98500.0,
    currency="MXN",
    due_date=date(2026, 7, 27),
    status="pending",
)


class FakeSmtp:
    """Records everything the notifier does, and can be told to fail."""

    instances: list["FakeSmtp"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages: list = []
        self.logins: list[tuple[str, str]] = []
        self.starttls_calls = 0
        self.tls_contexts: list = []
        self.closed = False
        self.fail_with: Exception | None = None
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.closed = True
        return False

    def starttls(self, context=None):
        self.starttls_calls += 1
        self.tls_contexts.append(context)

    def login(self, username, password):
        self.logins.append((username, password))

    def send_message(self, message):
        if self.fail_with is not None:
            raise self.fail_with
        self.messages.append(message)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


def make_notifier(**overrides) -> SmtpNotifier:
    kwargs = {
        "host": "localhost",
        "port": 1025,
        "sender_email": "cobranza@empresa.com",
        "sender_name": "Cobranza Empresa",
        "operations_email": "operaciones@empresa.com",
    }
    kwargs.update(overrides)
    return SmtpNotifier(**kwargs)


def make_config(**overrides) -> Config:
    kwargs = {
        "invoices_api_url": "",
        "api_token": "",
        "operations_email": "operaciones@empresa.com",
        "request_timeout": 10.0,
        "max_retries": 3,
        "retry_backoff_base": 0.5,
        "overdue_alert_threshold_days": 10,
        "state_file_path": Path("state/notifications.json"),
        "sample_data_path": Path("sample_data/invoices.json"),
        "log_level": "INFO",
        "dry_run": False,
        "notification_channel": "log",
        "smtp_host": "localhost",
        "smtp_port": 1025,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": False,
        "email_from": "cobranza@empresa.com",
        "email_from_name": "Cobranza Empresa",
    }
    kwargs.update(overrides)
    return Config(**kwargs)


# --- addressing -------------------------------------------------------------


def test_reminder_is_addressed_to_the_customer(fake_smtp):
    make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 32))

    message = fake_smtp.instances[0].messages[0]
    assert message["To"] == "pagos@logpacifico.mx"
    assert message["From"] == "Cobranza Empresa <cobranza@empresa.com>"
    assert "INV-1007" in message["Subject"]


def test_alert_is_addressed_to_operations_not_the_customer(fake_smtp):
    make_notifier().send(Notification(INVOICE, TYPE_OPERATIONS_ALERT, 32))

    message = fake_smtp.instances[0].messages[0]
    assert message["To"] == "operaciones@empresa.com"
    assert "32" in message["Subject"]


def parts(message) -> tuple[str, str]:
    """The plain text and HTML alternatives of a sent message."""
    return (
        message.get_body(preferencelist=("plain",)).get_content(),
        message.get_body(preferencelist=("html",)).get_content(),
    )


def test_reminder_carries_the_invoice_facts_in_both_parts(fake_smtp):
    make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 32))

    for body in parts(fake_smtp.instances[0].messages[0]):
        assert "INV-1007" in body
        assert "Logistica Pacifico" in body
        assert "$98,500.00 MXN" in body
        assert "27 de julio de 2026" in body
        assert "32 días" in body


def test_the_message_is_multipart_alternative_with_text_first(fake_smtp):
    """A client without HTML must find the readable version, not markup."""
    make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 32))

    message = fake_smtp.instances[0].messages[0]
    assert message.get_content_type() == "multipart/alternative"
    subtypes = [part.get_content_subtype() for part in message.iter_parts()]
    assert subtypes == ["plain", "html"]


def test_the_html_part_is_self_contained(fake_smtp):
    """No remote asset: it would be blocked by default and leak a read receipt."""
    make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 32))

    _, html_body = parts(fake_smtp.instances[0].messages[0])
    assert "http://" not in html_body
    assert "https://" not in html_body
    assert "<img" not in html_body


def test_customer_names_are_escaped_in_the_html(fake_smtp):
    """Invoice data is external input; it must not be able to inject markup."""
    hostile = replace(INVOICE, customer_name='Acme <script>alert("x")</script>')

    make_notifier().send(Notification(hostile, TYPE_REMINDER, 5))

    _, html_body = parts(fake_smtp.instances[0].messages[0])
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_alert_carries_the_customer_contact_in_both_parts(fake_smtp):
    """Operations needs a way to chase the invoice, not just its number."""
    make_notifier().send(Notification(INVOICE, TYPE_OPERATIONS_ALERT, 32))

    for body in parts(fake_smtp.instances[0].messages[0]):
        assert "pagos@logpacifico.mx" in body
        assert "Logistica Pacifico" in body


def test_a_single_day_is_not_pluralised(fake_smtp):
    make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 1))

    text, _ = parts(fake_smtp.instances[0].messages[0])
    assert "1 día" in text
    assert "1 días" not in text


def test_unknown_notification_type_raises(fake_smtp):
    with pytest.raises(ValueError, match="Unknown notification type"):
        make_notifier().send(Notification(INVOICE, "carrier_pigeon", 1))


# --- connection handling ----------------------------------------------------


def test_connection_uses_the_configured_host_port_and_timeout(fake_smtp):
    make_notifier(host="mailpit.local", port=2525, timeout=3.0).send(
        Notification(INVOICE, TYPE_REMINDER, 5)
    )

    connection = fake_smtp.instances[0]
    assert (connection.host, connection.port, connection.timeout) == ("mailpit.local", 2525, 3.0)
    assert connection.closed


def test_no_tls_and_no_login_when_not_configured(fake_smtp):
    """Mailpit needs neither, and asking for them would break the local demo."""
    make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 5))

    connection = fake_smtp.instances[0]
    assert connection.starttls_calls == 0
    assert connection.logins == []


def test_tls_and_credentials_are_used_when_configured(fake_smtp):
    make_notifier(use_tls=True, username="apikey", password="s3cr3t").send(
        Notification(INVOICE, TYPE_REMINDER, 5)
    )

    connection = fake_smtp.instances[0]
    assert connection.starttls_calls == 1
    assert connection.logins == [("apikey", "s3cr3t")]


def test_tls_verifies_the_certificate(fake_smtp):
    """smtplib's own default skips verification, which would expose the password."""
    make_notifier(use_tls=True, username="apikey", password="s3cr3t").send(
        Notification(INVOICE, TYPE_REMINDER, 5)
    )

    context = fake_smtp.instances[0].tls_contexts[0]
    assert context is not None, "starttls must receive an explicit SSL context"
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


# --- failures ---------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [smtplib.SMTPRecipientsRefused({}), ConnectionRefusedError("mailpit is down")],
)
def test_smtp_failures_become_notification_errors(fake_smtp, monkeypatch, error):
    def failing_smtp(*args, **kwargs):
        connection = FakeSmtp(*args, **kwargs)
        connection.fail_with = error
        return connection

    monkeypatch.setattr(smtplib, "SMTP", failing_smtp)

    with pytest.raises(NotificationError, match="smtp_delivery_failed invoice=INV-1007"):
        make_notifier().send(Notification(INVOICE, TYPE_REMINDER, 5))


# --- channel selection ------------------------------------------------------


def test_log_channel_builds_the_logging_notifier():
    assert isinstance(main.build_notifier(make_config()), LoggingNotifier)


def test_smtp_channel_builds_the_smtp_notifier():
    notifier = main.build_notifier(make_config(notification_channel="smtp"))

    assert isinstance(notifier, SmtpNotifier)
