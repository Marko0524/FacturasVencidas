"""Application configuration.

This is the ONLY module allowed to read environment variables. Every other
module receives a ``Config`` instance, which keeps the code testable and makes
the full set of knobs discoverable in a single place.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ConfigError(Exception):
    """Raised when an environment variable holds an invalid value."""


@dataclass(frozen=True)
class Config:
    """Immutable snapshot of the runtime configuration."""

    invoices_api_url: str
    api_token: str
    operations_email: str
    request_timeout: float
    max_retries: int
    retry_backoff_base: float
    overdue_alert_threshold_days: int
    state_file_path: Path
    sample_data_path: Path
    log_level: str
    dry_run: bool

    @property
    def uses_api(self) -> bool:
        """True when invoices must be pulled from the REST API."""
        return bool(self.invoices_api_url)

    @property
    def source(self) -> str:
        """Human readable data source, used for logging."""
        return "api" if self.uses_api else "file"


def load_config() -> Config:
    """Build a ``Config`` from the environment, validating every value.

    Defaults are chosen so that ``python main.py`` works on a fresh clone with
    no ``.env`` file and no network access.
    """
    return Config(
        invoices_api_url=_get_str("INVOICES_API_URL", ""),
        api_token=_get_str("API_TOKEN", ""),
        operations_email=_get_str("OPERATIONS_EMAIL", "operaciones@empresa.com"),
        request_timeout=_get_float("REQUEST_TIMEOUT", 10.0, minimum=0.1),
        max_retries=_get_int("MAX_RETRIES", 3, minimum=0),
        retry_backoff_base=_get_float("RETRY_BACKOFF_BASE", 0.5, minimum=0.0),
        overdue_alert_threshold_days=_get_int("OVERDUE_ALERT_THRESHOLD_DAYS", 10, minimum=0),
        state_file_path=_get_path("STATE_FILE_PATH", "./state/notifications.json"),
        sample_data_path=_get_path("SAMPLE_DATA_PATH", "./sample_data/invoices.json"),
        log_level=_get_log_level("LOG_LEVEL", "INFO"),
        dry_run=_get_bool("DRY_RUN", False),
    )


def configure_logging(log_level: str) -> None:
    """Set up stdout logging. The level is already validated by ``load_config``."""
    logging.basicConfig(
        level=log_level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=sys.stdout,
        force=True,
    )


def _get_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return default if raw is None else raw.strip()


def _get_int(name: str, default: int, minimum: int) -> int:
    raw = _raw_or_none(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _get_float(name: str, default: float, minimum: float) -> float:
    raw = _raw_or_none(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = _raw_or_none(name)
    if raw is None:
        return default
    normalized = raw.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _get_path(name: str, default: str) -> Path:
    """Resolve a path relative to the project root, so the app runs from any cwd."""
    raw = _raw_or_none(name) or default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _get_log_level(name: str, default: str) -> str:
    raw = (_raw_or_none(name) or default).upper()
    if not isinstance(logging.getLevelName(raw), int):
        raise ConfigError(f"{name} must be a valid logging level, got {raw!r}")
    return raw


def _raw_or_none(name: str) -> str | None:
    """Return the trimmed value, treating an empty variable as 'not set'."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()
