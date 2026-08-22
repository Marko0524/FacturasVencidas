"""Business rule tests. Deterministic: ``today`` is always injected."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.invoice_service import (
    TYPE_OPERATIONS_ALERT,
    TYPE_REMINDER,
    InvalidInvoiceError,
    Invoice,
    days_overdue,
    evaluate_invoice,
    parse_invoice,
    parse_invoices,
)

TODAY = date(2026, 8, 21)
THRESHOLD = 10


def make_invoice(overdue_days: int, status: str = "pending", **overrides) -> Invoice:
    """Build an invoice that is ``overdue_days`` past due relative to TODAY."""
    fields = {
        "id": "INV-1001",
        "customer_name": "Empresa Demo",
        "customer_email": "cliente@empresa.com",
        "amount": 15000.50,
        "currency": "MXN",
        "due_date": TODAY - timedelta(days=overdue_days),
        "status": status,
    }
    fields.update(overrides)
    return Invoice(**fields)


def make_record(**overrides) -> dict:
    record = {
        "id": "INV-1001",
        "customer_name": "Empresa Demo",
        "customer_email": "cliente@empresa.com",
        "amount": 15000.50,
        "currency": "MXN",
        "due_date": "2026-08-05",
        "status": "pending",
    }
    record.update(overrides)
    return record


# --- days_overdue -----------------------------------------------------------


def test_days_overdue_is_a_positive_integer_for_past_due_dates():
    assert days_overdue(make_invoice(overdue_days=6), TODAY) == 6


def test_days_overdue_is_negative_for_future_due_dates():
    assert days_overdue(make_invoice(overdue_days=-4), TODAY) == -4


# --- no action --------------------------------------------------------------


def test_invoice_not_yet_due_generates_no_notification():
    assert evaluate_invoice(make_invoice(overdue_days=-7), TODAY, THRESHOLD) == []


def test_invoice_due_today_generates_no_notification():
    assert evaluate_invoice(make_invoice(overdue_days=0), TODAY, THRESHOLD) == []


def test_paid_invoice_generates_no_notification_even_when_overdue():
    assert evaluate_invoice(make_invoice(overdue_days=30, status="paid"), TODAY, THRESHOLD) == []


def test_cancelled_invoice_generates_no_notification_even_when_overdue():
    assert evaluate_invoice(make_invoice(overdue_days=40, status="cancelled"), TODAY, THRESHOLD) == []


# --- reminder only ----------------------------------------------------------


@pytest.mark.parametrize("overdue", [1, 5, 9, 10])
def test_invoice_up_to_threshold_generates_only_a_reminder(overdue):
    notifications = evaluate_invoice(make_invoice(overdue_days=overdue), TODAY, THRESHOLD)

    assert [n.type for n in notifications] == [TYPE_REMINDER]
    assert notifications[0].days_overdue == overdue


def test_exactly_ten_days_overdue_is_the_reminder_only_boundary():
    notifications = evaluate_invoice(make_invoice(overdue_days=10), TODAY, THRESHOLD)

    assert [n.type for n in notifications] == [TYPE_REMINDER]


# --- reminder + operations alert -------------------------------------------


def test_eleven_days_overdue_also_generates_an_operations_alert():
    notifications = evaluate_invoice(make_invoice(overdue_days=11), TODAY, THRESHOLD)

    assert [n.type for n in notifications] == [TYPE_REMINDER, TYPE_OPERATIONS_ALERT]
    assert all(n.days_overdue == 11 for n in notifications)


def test_far_overdue_invoice_generates_reminder_and_alert():
    notifications = evaluate_invoice(make_invoice(overdue_days=25), TODAY, THRESHOLD)

    assert [n.type for n in notifications] == [TYPE_REMINDER, TYPE_OPERATIONS_ALERT]


def test_threshold_is_configurable():
    notifications = evaluate_invoice(make_invoice(overdue_days=6), TODAY, threshold_days=5)

    assert [n.type for n in notifications] == [TYPE_REMINDER, TYPE_OPERATIONS_ALERT]


# --- parsing and robustness -------------------------------------------------


def test_parse_invoice_returns_a_typed_invoice():
    invoice = parse_invoice(make_record())

    assert invoice.id == "INV-1001"
    assert invoice.due_date == date(2026, 8, 5)
    assert invoice.amount == pytest.approx(15000.50)


def test_parse_invoice_normalises_status_case():
    assert parse_invoice(make_record(status="PENDING")).status == "pending"


@pytest.mark.parametrize(
    "overrides, expected_reason",
    [
        ({"due_date": "2026-13-45"}, "invalid_due_date"),
        ({"due_date": "not-a-date"}, "invalid_due_date"),
        ({"due_date": None}, "missing_or_invalid_field:due_date"),
        ({"customer_email": ""}, "missing_or_invalid_field:customer_email"),
        ({"amount": None}, "missing_or_invalid_field:amount"),
        ({"amount": "abc"}, "invalid_amount"),
    ],
)
def test_parse_invoice_rejects_malformed_records(overrides, expected_reason):
    with pytest.raises(InvalidInvoiceError) as exc:
        parse_invoice(make_record(**overrides))

    assert str(exc.value) == expected_reason


def test_parse_invoices_isolates_bad_records_without_raising():
    records = [
        make_record(id="INV-1001"),
        make_record(id="INV-9999", due_date="2026-13-45"),
        make_record(id="INV-1002"),
    ]

    valid, rejected = parse_invoices(records)

    assert [i.id for i in valid] == ["INV-1001", "INV-1002"]
    assert [(r.invoice_id, r.reason) for r in rejected] == [("INV-9999", "invalid_due_date")]


def test_parse_invoices_labels_records_without_a_usable_id():
    valid, rejected = parse_invoices(["not-an-object"])

    assert valid == []
    assert rejected[0].invoice_id == "<record#0>"
    assert rejected[0].reason == "not_an_object"
