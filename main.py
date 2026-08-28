"""Entry point: orchestrates fetch -> validate -> evaluate -> notify."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.api_client import ApiError, fetch_raw_invoices
from app.config import Config, ConfigError, configure_logging, load_config
from app.idempotency import NotificationStore, build_key
from app.invoice_service import (
    TYPE_OPERATIONS_ALERT,
    TYPE_REMINDER,
    Notification,
    evaluate_invoice,
    parse_invoices,
)
from app.notifier import LoggingNotifier, SmtpNotifier

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """Counters for the final log line and the process exit code."""

    fetched: int = 0
    invalid: int = 0
    overdue: int = 0
    reminders: int = 0
    alerts: int = 0
    skipped: int = 0
    errors: int = 0

    def as_log_fields(self) -> str:
        return (
            f"fetched={self.fetched} invalid={self.invalid} overdue={self.overdue} "
            f"reminders={self.reminders} alerts={self.alerts} "
            f"skipped={self.skipped} errors={self.errors}"
        )


def build_notifier(config: Config) -> Any:
    """Pick the delivery channel declared by the configuration.

    ``log`` keeps the process side-effect free; ``smtp`` sends real mail, which
    against Mailpit lands in a local inbox and against Microsoft 365 / SendGrid
    lands in the customer's.
    """
    if config.sends_real_email:
        return SmtpNotifier(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.smtp_username,
            password=config.smtp_password,
            use_tls=config.smtp_use_tls,
            sender_email=config.email_from,
            sender_name=config.email_from_name,
            operations_email=config.operations_email,
            timeout=config.request_timeout,
        )
    return LoggingNotifier(config.operations_email)


def run(config: Config, today: date | None = None, notifier: Any | None = None) -> RunSummary:
    """Run one full pass and return its summary.

    ``today`` and ``notifier`` are injectable, which is what lets the tests run
    deterministically and without side effects.
    """
    run_date = today if today is not None else date.today()
    summary = RunSummary()

    logger.info(
        "Process started source=%s run_date=%s threshold_days=%d channel=%s dry_run=%s",
        config.source,
        run_date.isoformat(),
        config.overdue_alert_threshold_days,
        config.notification_channel,
        config.dry_run,
    )

    records = fetch_raw_invoices(config)
    summary.fetched = len(records)
    logger.info("Invoices fetched count=%d", summary.fetched)

    invoices, rejected = parse_invoices(records)
    summary.invalid = len(rejected)
    for item in rejected:
        logger.warning("Invoice discarded invoice=%s reason=%s", item.invoice_id, item.reason)

    store = NotificationStore(config.state_file_path, dry_run=config.dry_run)
    active_notifier = notifier if notifier is not None else build_notifier(config)

    for invoice in invoices:
        notifications = evaluate_invoice(invoice, run_date, config.overdue_alert_threshold_days)
        if not notifications:
            continue

        summary.overdue += 1
        logger.info(
            "Invoice is overdue invoice=%s days_overdue=%d due_date=%s",
            invoice.id,
            notifications[0].days_overdue,
            invoice.due_date.isoformat(),
        )
        for notification in notifications:
            _dispatch(notification, store, active_notifier, run_date, summary)

    logger.info("Process finished %s", summary.as_log_fields())
    return summary


def _dispatch(
    notification: Notification,
    store: NotificationStore,
    notifier: Any,
    run_date: date,
    summary: RunSummary,
) -> None:
    """Send one notification unless it was already sent for this run date."""
    key = build_key(notification.invoice.id, notification.type, run_date)

    if store.was_processed(key):
        summary.skipped += 1
        logger.info(
            "Notification skipped reason=already_processed invoice=%s type=%s",
            notification.invoice.id,
            notification.type,
        )
        return

    try:
        notifier.send(notification)
    except Exception:  # noqa: BLE001 - one bad notification must not stop the batch
        summary.errors += 1
        logger.exception(
            "Notification failed invoice=%s type=%s",
            notification.invoice.id,
            notification.type,
        )
        return

    # Marked AFTER a successful send: at-least-once. See the README trade-off.
    store.mark_processed(key, run_date)

    if notification.type == TYPE_REMINDER:
        summary.reminders += 1
    elif notification.type == TYPE_OPERATIONS_ALERT:
        summary.alerts += 1


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    configure_logging(config.log_level)

    try:
        summary = run(config)
    except ApiError as exc:
        logger.error("Process failed reason=api_error detail=%s", exc)
        return 1
    except Exception:  # noqa: BLE001 - scheduled job: log and exit non-zero
        logger.exception("Process failed reason=unexpected_error")
        return 1

    return 1 if summary.errors else 0


if __name__ == "__main__":
    sys.exit(main())
