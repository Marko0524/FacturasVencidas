"""Idempotency tests, including a full second run that must send nothing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import main
from app.config import Config
from app.idempotency import NotificationStore, build_key
from app.invoice_service import TYPE_OPERATIONS_ALERT, TYPE_REMINDER

TODAY = date(2026, 8, 21)


class RecordingNotifier:
    """Captures notifications instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, notification) -> None:
        self.sent.append((notification.invoice.id, notification.type))


def make_config(tmp_path: Path, invoices: list[dict], dry_run: bool = False) -> Config:
    sample_path = tmp_path / "invoices.json"
    sample_path.write_text(json.dumps({"data": invoices}), encoding="utf-8")
    return Config(
        invoices_api_url="",
        api_token="",
        operations_email="operaciones@empresa.com",
        request_timeout=10.0,
        max_retries=3,
        retry_backoff_base=0.5,
        overdue_alert_threshold_days=10,
        state_file_path=tmp_path / "state" / "notifications.json",
        sample_data_path=sample_path,
        log_level="INFO",
        dry_run=dry_run,
    )


def invoice_record(invoice_id: str, due_date: str, status: str = "pending") -> dict:
    return {
        "id": invoice_id,
        "customer_name": "Empresa Demo",
        "customer_email": "cliente@empresa.com",
        "amount": 1000.0,
        "currency": "MXN",
        "due_date": due_date,
        "status": status,
    }


# --- store behaviour --------------------------------------------------------


def test_unknown_key_is_not_processed(tmp_path: Path):
    store = NotificationStore(tmp_path / "state.json")

    assert store.was_processed("anything") is False


def test_marked_key_survives_a_new_store_instance(tmp_path: Path):
    path = tmp_path / "state" / "notifications.json"
    key = build_key("INV-1001", TYPE_REMINDER, TODAY)

    NotificationStore(path).mark_processed(key, TODAY)

    assert NotificationStore(path).was_processed(key) is True


def test_state_file_is_written_atomically_leaving_no_temp_file(tmp_path: Path):
    path = tmp_path / "state" / "notifications.json"

    NotificationStore(path).mark_processed(build_key("INV-1001", TYPE_REMINDER, TODAY), TODAY)

    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []


def test_corrupt_state_file_does_not_break_the_run(tmp_path: Path):
    path = tmp_path / "notifications.json"
    path.write_text("{ this is not json", encoding="utf-8")

    store = NotificationStore(path)

    assert store.size == 0
    assert store.was_processed("anything") is False


def test_dry_run_does_not_persist_state(tmp_path: Path):
    path = tmp_path / "notifications.json"
    store = NotificationStore(path, dry_run=True)

    store.mark_processed(build_key("INV-1001", TYPE_REMINDER, TODAY), TODAY)

    assert path.exists() is False


def test_key_is_scoped_per_invoice_type_and_day():
    base = build_key("INV-1001", TYPE_REMINDER, TODAY)

    assert base != build_key("INV-1002", TYPE_REMINDER, TODAY)
    assert base != build_key("INV-1001", TYPE_OPERATIONS_ALERT, TODAY)
    assert base != build_key("INV-1001", TYPE_REMINDER, date(2026, 8, 22))


# --- end to end -------------------------------------------------------------


def test_second_run_on_the_same_day_sends_nothing(tmp_path: Path):
    config = make_config(
        tmp_path,
        [
            invoice_record("INV-1003", "2026-08-16"),  # 5 days overdue
            invoice_record("INV-1005", "2026-08-06"),  # 15 days overdue
        ],
    )

    first_notifier = RecordingNotifier()
    first = main.run(config, today=TODAY, notifier=first_notifier)

    second_notifier = RecordingNotifier()
    second = main.run(config, today=TODAY, notifier=second_notifier)

    assert (first.reminders, first.alerts, first.skipped) == (2, 1, 0)
    assert len(first_notifier.sent) == 3

    assert (second.reminders, second.alerts) == (0, 0)
    assert second.skipped == 3
    assert second_notifier.sent == []


def test_next_day_run_notifies_again(tmp_path: Path):
    config = make_config(tmp_path, [invoice_record("INV-1003", "2026-08-16")])

    main.run(config, today=TODAY, notifier=RecordingNotifier())
    notifier = RecordingNotifier()
    summary = main.run(config, today=date(2026, 8, 22), notifier=notifier)

    assert summary.reminders == 1
    assert summary.skipped == 0
    assert notifier.sent == [("INV-1003", TYPE_REMINDER)]


def test_run_counts_invalid_records_without_failing(tmp_path: Path):
    config = make_config(
        tmp_path,
        [
            invoice_record("INV-1003", "2026-08-16"),
            invoice_record("INV-9999", "2026-13-45"),
            invoice_record("INV-1001", "2026-08-01", status="paid"),
        ],
    )

    summary = main.run(config, today=TODAY, notifier=RecordingNotifier())

    assert summary.fetched == 3
    assert summary.invalid == 1
    assert summary.overdue == 1
    assert summary.reminders == 1
    assert summary.errors == 0
