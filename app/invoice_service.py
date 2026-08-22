"""Business rules.

Everything in this module is a pure function: no I/O, no network, no logging,
no clock reads. ``today`` is always an explicit parameter, which is what makes
the rules deterministic and trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

STATUS_PENDING = "pending"

TYPE_REMINDER = "reminder"
TYPE_OPERATIONS_ALERT = "operations_alert"


class InvalidInvoiceError(ValueError):
    """Raised when a raw record cannot be parsed into an ``Invoice``.

    The message is a short, space-free reason code so it can be logged as a
    ``key=value`` field.
    """


@dataclass(frozen=True)
class Invoice:
    """A validated invoice."""

    id: str
    customer_name: str
    customer_email: str
    amount: float
    currency: str
    due_date: date
    status: str


@dataclass(frozen=True)
class Notification:
    """An action the system decided to take for an invoice."""

    invoice: Invoice
    type: str
    days_overdue: int


@dataclass(frozen=True)
class RejectedInvoice:
    """A raw record that failed validation, kept so the caller can log it."""

    invoice_id: str
    reason: str


def parse_invoices(records: Iterable[Any]) -> tuple[list[Invoice], list[RejectedInvoice]]:
    """Validate raw records, returning the valid invoices and the rejected ones.

    A malformed record never aborts the batch: it is isolated and reported.
    """
    valid: list[Invoice] = []
    rejected: list[RejectedInvoice] = []
    for index, record in enumerate(records):
        try:
            valid.append(parse_invoice(record))
        except InvalidInvoiceError as exc:
            rejected.append(RejectedInvoice(_record_id(record, index), str(exc)))
    return valid, rejected


def parse_invoice(record: Any) -> Invoice:
    """Convert a raw record into an ``Invoice`` or raise ``InvalidInvoiceError``."""
    if not isinstance(record, dict):
        raise InvalidInvoiceError("not_an_object")
    return Invoice(
        id=_required_str(record, "id"),
        customer_name=_required_str(record, "customer_name"),
        customer_email=_required_str(record, "customer_email"),
        amount=_required_amount(record),
        currency=_required_str(record, "currency"),
        due_date=_required_date(record, "due_date"),
        status=_required_str(record, "status").lower(),
    )


def days_overdue(invoice: Invoice, today: date) -> int:
    """Whole days past the due date. Zero or negative means still current."""
    return (today - invoice.due_date).days


def evaluate_invoice(invoice: Invoice, today: date, threshold_days: int) -> list[Notification]:
    """Decide which notifications an invoice deserves.

    * Not ``pending``            -> no action (paid, cancelled, unknown states).
    * Due today or in the future -> no action.
    * 1..threshold days overdue  -> customer reminder only.
    * More than threshold days   -> customer reminder + operations alert.
    """
    if invoice.status != STATUS_PENDING:
        return []

    overdue = days_overdue(invoice, today)
    if overdue <= 0:
        return []

    notifications = [Notification(invoice, TYPE_REMINDER, overdue)]
    if overdue > threshold_days:
        notifications.append(Notification(invoice, TYPE_OPERATIONS_ALERT, overdue))
    return notifications


def _required_str(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidInvoiceError(f"missing_or_invalid_field:{field}")
    return value.strip()


def _required_amount(record: dict[str, Any]) -> float:
    value = record.get("amount")
    if value is None or isinstance(value, bool):
        raise InvalidInvoiceError("missing_or_invalid_field:amount")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInvoiceError("invalid_amount") from exc


def _required_date(record: dict[str, Any], field: str) -> date:
    raw = record.get(field)
    if not isinstance(raw, str):
        raise InvalidInvoiceError(f"missing_or_invalid_field:{field}")
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise InvalidInvoiceError(f"invalid_{field}") from exc


def _record_id(record: Any, index: int) -> str:
    if isinstance(record, dict):
        raw_id = record.get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            return raw_id.strip()
    return f"<record#{index}>"
