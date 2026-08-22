"""Idempotency for notifications.

The unit of idempotency is the ACTION, not the API read: a key identifies one
notification of one type, for one invoice, on one run date.

Storage here is a local JSON file, which is enough to demonstrate the mechanism
and easy to inspect during a review. See the README for the production swap.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_VERSION = 1


def build_key(invoice_id: str, notification_type: str, run_date: date) -> str:
    """Build the idempotency key.

    ``invoice_id | type | run_date`` means: at most ONE notification of each type
    per invoice per day. Re-running the job (or retrying it after a crash) on the
    same day sends nothing extra; the next day the customer is reminded again,
    which is the point of a dunning process.
    """
    return f"{invoice_id}|{notification_type}|{run_date.isoformat()}"


class NotificationStore:
    """Tracks which notifications have already been sent."""

    def __init__(self, path: Path, dry_run: bool = False) -> None:
        self._path = path
        self._dry_run = dry_run
        self._processed: dict[str, str] = self._load()

    def was_processed(self, key: str) -> bool:
        return key in self._processed

    def mark_processed(self, key: str, run_date: date) -> None:
        """Record a key and persist immediately.

        Writing through on every notification keeps the state correct even if the
        process dies mid-run. The batches here are small, so the extra writes are
        cheap; at scale this would be a single batched write or a real store.
        """
        self._processed[key] = run_date.isoformat()
        if self._dry_run:
            logger.debug("Dry run: state not persisted key=%s", key)
            return
        self._persist()

    @property
    def size(self) -> int:
        return len(self._processed)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            logger.info("No previous notification state found path=%s", self._path)
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            processed = payload["processed"]
            if not isinstance(processed, dict):
                raise ValueError("'processed' must be an object")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # A corrupt state file must not stop the run. Worst case we re-send.
            logger.warning(
                "Notification state unreadable, starting from empty path=%s reason=%s",
                self._path,
                type(exc).__name__,
            )
            return {}
        logger.info("Notification state loaded path=%s entries=%d", self._path, len(processed))
        return dict(processed)

    def _persist(self) -> None:
        """Atomic write: a crash mid-write can never corrupt the state file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": STATE_VERSION, "processed": self._processed}
        temp_path = self._path.parent / f"{self._path.name}.tmp"
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp_path, self._path)
