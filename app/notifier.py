"""Notification delivery.

Delivery is simulated with ``logging``. Swapping in SendGrid, Microsoft Graph or
Azure Communication Services means writing a class with these same two methods
and passing it to ``main.run`` -- no business rule changes.
"""

from __future__ import annotations

import logging

from app.invoice_service import (
    TYPE_OPERATIONS_ALERT,
    TYPE_REMINDER,
    Invoice,
    Notification,
)

logger = logging.getLogger(__name__)


class LoggingNotifier:
    """Simulates sending notifications by writing structured log lines."""

    def __init__(self, operations_email: str) -> None:
        self._operations_email = operations_email

    def send(self, notification: Notification) -> None:
        """Dispatch a notification to the right channel."""
        if notification.type == TYPE_REMINDER:
            self.send_payment_reminder(notification.invoice)
        elif notification.type == TYPE_OPERATIONS_ALERT:
            self.send_operations_alert(notification.invoice, notification.days_overdue)
        else:
            raise ValueError(f"Unknown notification type: {notification.type}")

    def send_payment_reminder(self, invoice: Invoice) -> None:
        logger.info(
            'Payment reminder sent to=%s invoice=%s customer="%s" amount=%.2f currency=%s due_date=%s',
            invoice.customer_email,
            invoice.id,
            invoice.customer_name,
            invoice.amount,
            invoice.currency,
            invoice.due_date.isoformat(),
        )

    def send_operations_alert(self, invoice: Invoice, days_overdue: int) -> None:
        logger.warning(
            'Operations alert sent to=%s invoice=%s customer="%s" days_overdue=%d amount=%.2f currency=%s',
            self._operations_email,
            invoice.id,
            invoice.customer_name,
            days_overdue,
            invoice.amount,
            invoice.currency,
        )
