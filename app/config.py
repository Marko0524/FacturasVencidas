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
ENV_FILE_PATH = PROJECT_ROOT / ".env"

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

CHANNEL_LOG = "log"
CHANNEL_SMTP = "smtp"
NOTIFICATION_CHANNELS = (CHANNEL_LOG, CHANNEL_SMTP)


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
    notification_channel: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    email_from: str
    email_from_name: str

    @property
    def uses_api(self) -> bool:
        """True when invoices must be pulled from the REST API."""
        return bool(self.invoices_api_url)

    @property
    def source(self) -> str:
        """Human readable data source, used for logging."""
        return "api" if self.uses_api else "file"

    @property
    def sends_real_email(self) -> bool:
        """True when notifications leave the process as SMTP mail."""
        return self.notification_channel == CHANNEL_SMTP


def load_config() -> Config:
    """Build a ``Config`` from the environment, validating every value.

    A local ``.env`` is loaded first, so a secret can live in a gitignored file
    instead of a shell command. Defaults are chosen so that ``python main.py``
    works on a fresh clone with no ``.env`` file and no network access: the
    notification channel defaults to ``log``, so nothing is ever mailed by
    accident.
    """
    load_env_file()
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
        notification_channel=_get_channel("NOTIFICATION_CHANNEL", CHANNEL_LOG),
        smtp_host=_get_str("SMTP_HOST", "localhost"),
        smtp_port=_get_int("SMTP_PORT", 1025, minimum=1),
        smtp_username=_get_str("SMTP_USERNAME", ""),
        smtp_password=_get_str("SMTP_PASSWORD", ""),
        smtp_use_tls=_get_bool("SMTP_USE_TLS", False),
        email_from=_get_str("EMAIL_FROM", "cobranza@empresa.com"),
        email_from_name=_get_str("EMAIL_FROM_NAME", "Cobranza Empresa"),
    )


def load_env_file(path: Path | None = None) -> None:
    """Copy ``KEY=VALUE`` lines from a ``.env`` file into the environment.

    The README has always said ``cp .env.example .env``; this is what makes that
    work for a local run, the same way ``docker run --env-file`` does for the
    container. It exists so an SMTP password can sit in a gitignored file instead
    of being typed into a shell command and left in the history.

    **The real environment always wins**, so an explicit ``SMTP_HOST=... python
    main.py`` still overrides the file. A missing file is not an error: the
    defaults are meant to be enough.

    Deliberately minimal: no interpolation, no multi-line values, no extra
    dependency. Anything richer belongs to ``python-dotenv``.
    """
    env_path = ENV_FILE_PATH if path is None else path
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not name or name in os.environ:
            continue
        os.environ[name] = _strip_quotes(raw_value.strip())


def _strip_quotes(value: str) -> str:
    """Unwrap a value written as 'foo' or "foo", so quotes are not part of it."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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


def _get_channel(name: str, default: str) -> str:
    raw = (_raw_or_none(name) or default).lower()
    if raw not in NOTIFICATION_CHANNELS:
        allowed = "/".join(NOTIFICATION_CHANNELS)
        raise ConfigError(f"{name} must be one of {allowed}, got {raw!r}")
    return raw


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
