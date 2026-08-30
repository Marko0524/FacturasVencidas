"""Assistant configuration.

Same rule as the reminder job: this is the only module that reads the
environment. Everything else receives a ``Settings`` instance, so the full set
of knobs is discoverable in one place and the tests never touch ``os.environ``.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.auth import parse_links

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ASSISTANT_ROOT = BACKEND_ROOT.parent
REPO_ROOT = ASSISTANT_ROOT.parent

PROVIDER_AZURE = "azure"
PROVIDER_GEMINI = "gemini"
PROVIDER_VERTEX = "vertex"
PROVIDER_FAKE = "fake"
PROVIDERS = (PROVIDER_AZURE, PROVIDER_GEMINI, PROVIDER_VERTEX, PROVIDER_FAKE)

# Below this cosine similarity a fragment is not evidence, it is noise. Without
# a floor the retriever always hands back its best guess, and the assistant
# answers confidently about something nobody asked.
#
# The floor is per provider because cosine similarity is not a universal scale.
# Two trained embedding models put related text around 0.7-0.9; the fake
# provider's bag of words peaks near 0.27 for a good match, because it only
# scores literal word overlap. One shared constant would either muzzle the real
# providers or wave everything through on the fake one.
DEFAULT_MIN_SIMILARITY = {
    PROVIDER_AZURE: 0.55,
    PROVIDER_GEMINI: 0.55,
    PROVIDER_VERTEX: 0.55,
    PROVIDER_FAKE: 0.15,
}


class ConfigError(Exception):
    """Raised when an environment variable is missing or holds a bad value."""


@dataclass(frozen=True)
class AzureSettings:
    """Azure OpenAI. Deployments are named by whoever created them, so the
    deployment name is configuration, never a hardcoded model id."""

    endpoint: str
    api_key: str
    chat_deployment: str
    embedding_deployment: str
    api_version: str

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.chat_deployment)


@dataclass(frozen=True)
class GeminiSettings:
    """Google AI Studio. Here the model id *is* the address."""

    api_key: str
    chat_model: str
    embedding_model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class VertexSettings:
    """Vertex AI with a service account: no static key anywhere."""

    credentials_path: Path | None
    project: str
    location: str
    chat_model: str
    embedding_model: str

    @property
    def configured(self) -> bool:
        return self.credentials_path is not None and self.credentials_path.is_file()


BACKEND_MEMORY = "memoria"
BACKEND_POSTGRES = "postgres"
RETRIEVAL_BACKENDS = (BACKEND_MEMORY, BACKEND_POSTGRES)

AUTH_DEMO = "demo"
AUTH_LOCAL = "local"
AUTH_GOOGLE = "google"
AUTH_MODES = (AUTH_DEMO, AUTH_LOCAL, AUTH_GOOGLE)


@dataclass(frozen=True)
class Settings:
    provider: str
    retrieval_backend: str
    database_url: str
    auth_mode: str
    google_client_id: str
    account_links: dict
    session_secret: str
    seed_password: str
    azure: AzureSettings
    gemini: GeminiSettings
    vertex: VertexSettings
    corpus_path: Path
    invoices_path: Path
    request_timeout: float
    top_k: int
    min_similarity: float
    max_question_chars: int
    overdue_alert_threshold_days: int
    log_level: str
    # La interfaz ya compilada. `None` en desarrollo, donde la sirve Vite con su
    # recarga en caliente; una ruta dentro del contenedor cuando se despliega.
    # Va al final y con valor por defecto para no obligar a nombrarlo a quien
    # construye un `Settings` a mano, que es lo que hacen todas las pruebas.
    static_path: Path | None = None


def load_env_file(path: Path | None = None) -> None:
    """Copy ``KEY=VALUE`` lines from ``asistente/.env`` into the environment.

    So an API key can sit in a gitignored file instead of a shell command that
    survives in the history. The real environment always wins, which keeps
    ``LLM_PROVIDER=gemini python -m uvicorn ...`` working as an override.
    """
    env_path = ASSISTANT_ROOT / ".env" if path is None else path
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
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name] = value


def load_settings() -> Settings:
    """Build ``Settings`` from the environment, validating every value."""
    load_env_file()
    provider = _get_str("LLM_PROVIDER", PROVIDER_FAKE).lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of {'/'.join(PROVIDERS)}, got {provider!r}")

    backend = _get_str("RETRIEVAL_BACKEND", BACKEND_MEMORY).lower()
    if backend not in RETRIEVAL_BACKENDS:
        raise ConfigError(
            f"RETRIEVAL_BACKEND must be one of {'/'.join(RETRIEVAL_BACKENDS)}, got {backend!r}"
        )

    auth_mode = _get_str("AUTH_MODE", AUTH_DEMO).lower()
    if auth_mode not in AUTH_MODES:
        raise ConfigError(f"AUTH_MODE must be one of {'/'.join(AUTH_MODES)}, got {auth_mode!r}")

    if auth_mode == AUTH_LOCAL and backend != BACKEND_POSTGRES:
        # Los usuarios viven en Postgres. Arrancar en `local` sin base de datos
        # dejaría un formulario de acceso que no puede aceptar a nadie.
        raise ConfigError("AUTH_MODE=local requiere RETRIEVAL_BACKEND=postgres")

    client_id = _get_str("GOOGLE_CLIENT_ID", "")
    if auth_mode == AUTH_GOOGLE and not client_id:
        # Failing here is the point: starting in `google` mode without a client
        # id would verify every token against nothing and accept all of them.
        raise ConfigError("AUTH_MODE=google requires GOOGLE_CLIENT_ID")

    return Settings(
        provider=provider,
        retrieval_backend=backend,
        auth_mode=auth_mode,
        google_client_id=client_id,
        # "correo=cliente,correo=cliente" — qué cuenta de cliente puede leer
        # cada cuenta de Google verificada.
        account_links=parse_links(_get_str("ACCOUNT_LINKS", "")),
        # Firma los tokens de sesión. Si no se configura, se genera uno al
        # arrancar: las sesiones siguen siendo seguras, pero no sobreviven a un
        # reinicio. Un valor por omisión fijo sería mucho peor —cualquiera que
        # leyera el código podría firmarse una sesión— así que no lo hay.
        session_secret=_get_str("SESSION_SECRET", "") or _secreto_efimero(),
        static_path=(Path(_get_str("STATIC_PATH", "")) if _get_str("STATIC_PATH", "") else None),
        seed_password=_get_str("SEED_PASSWORD", "") or "asistente2026",
        # Only meaningful with the postgres backend; the default matches
        # docker-compose.yml so `docker compose up -d` is the whole setup.
        database_url=_get_str(
            "DATABASE_URL", "postgresql://asistente:asistente@localhost:5432/asistente"
        ),
        azure=AzureSettings(
            endpoint=_get_str("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
            api_key=_get_str("AZURE_OPENAI_API_KEY", ""),
            chat_deployment=_get_str("AZURE_OPENAI_CHAT_DEPLOYMENT", ""),
            embedding_deployment=_get_str("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
            api_version=_get_str("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        ),
        gemini=GeminiSettings(
            api_key=_get_str("GEMINI_API_KEY", ""),
            # Model ids retire. Google returns a 404 naming the replacement
            # ("no longer available to new users"), and `GET /v1beta/models`
            # lists what a given key can actually reach — which is why this is
            # configuration and not a constant buried in the provider.
            chat_model=_get_str("GEMINI_CHAT_MODEL", "gemini-3.6-flash"),
            embedding_model=_get_str("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"),
        ),
        vertex=VertexSettings(
            # The standard Google variable name, so `gcloud` and every other
            # tool on the machine point at the same credential.
            credentials_path=_optional_path("GOOGLE_APPLICATION_CREDENTIALS"),
            # Empty means "whatever project the credential belongs to".
            project=_get_str("VERTEX_PROJECT", ""),
            # `global` reaches the newest models; a region is required for some
            # older ones and for data-residency commitments.
            location=_get_str("VERTEX_LOCATION", "global"),
            chat_model=_get_str("VERTEX_CHAT_MODEL", "gemini-2.5-flash"),
            embedding_model=_get_str("VERTEX_EMBEDDING_MODEL", "gemini-embedding-001"),
        ),
        corpus_path=_get_path("CORPUS_PATH", ASSISTANT_ROOT / "data" / "polizas"),
        invoices_path=_get_path("INVOICES_PATH", REPO_ROOT / "sample_data" / "invoices.json"),
        request_timeout=_get_float("LLM_REQUEST_TIMEOUT", 30.0, minimum=1.0),
        top_k=_get_int("RETRIEVAL_TOP_K", 4, minimum=1),
        min_similarity=_get_float(
            "RETRIEVAL_MIN_SIMILARITY", DEFAULT_MIN_SIMILARITY[provider], minimum=0.0
        ),
        max_question_chars=_get_int("MAX_QUESTION_CHARS", 800, minimum=1),
        # La misma variable que lee el job de recordatorios. Leerla de aquí, y
        # no copiar el número, es lo que impide que el asistente diga "rebasa
        # el umbral" sobre una factura que el job no escaló.
        overdue_alert_threshold_days=_get_int("OVERDUE_ALERT_THRESHOLD_DAYS", 10, minimum=0),
        log_level=_get_str("LOG_LEVEL", "INFO").upper(),
    )


def _secreto_efimero() -> str:
    import secrets

    # Cloud Run pone K_SERVICE. Ahí un secreto por proceso no es un inconveniente,
    # es un fallo que no se ve: el servicio escala a varias instancias, cada una
    # firma con una clave distinta, y el token que emitió una lo rechaza la
    # siguiente. Al usuario le aparecen cierres de sesión sueltos, sin error en
    # ningún log, y el culpable —una variable sin poner— está lejísimos del
    # síntoma. Mejor no arrancar.
    if os.getenv("K_SERVICE"):
        raise RuntimeError(
            "SESSION_SECRET es obligatorio en Cloud Run: sin él cada instancia "
            "firma con una clave distinta y las sesiones se caen al azar. "
            "Pásalo con --set-secrets SESSION_SECRET=asistente-session-secret:latest"
        )

    logger = logging.getLogger(__name__)
    logger.warning(
        "SESSION_SECRET no está configurado: se generó uno para este proceso. "
        "Las sesiones abiertas se invalidarán al reiniciar."
    )
    return secrets.token_urlsafe(32)


def configure_logging(log_level: str) -> None:
    """Set up stdout logging, matching the reminder job's format."""
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
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


def _optional_path(name: str) -> Path | None:
    """A path only if the variable is set; ``None`` otherwise, not a guess."""
    raw = _raw_or_none(name)
    return Path(raw).expanduser() if raw else None


def _get_path(name: str, default: Path) -> Path:
    raw = _raw_or_none(name)
    if raw is None:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def _raw_or_none(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()
