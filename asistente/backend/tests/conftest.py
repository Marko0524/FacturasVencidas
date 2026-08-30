"""Shared fixtures. Everything here is offline and deterministic."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import (  # noqa: E402
    AUTH_DEMO,
    BACKEND_MEMORY,
    PROVIDER_FAKE,
    AzureSettings,
    GeminiSettings,
    Settings,
    VertexSettings,
)
from app.invoices import InvoiceStore  # noqa: E402
from app.providers.fake import FakeProvider  # noqa: E402
from app.retrieval import Retriever, load_corpus  # noqa: E402

ASSISTANT_ROOT = BACKEND_ROOT.parent
REPO_ROOT = ASSISTANT_ROOT.parent

LOGISTICA = "kayelo3614@neowd.com"
MERIDIANO = "finanzas@meridiano.mx"
AURORA = "pagos@aurora.mx"


@pytest.fixture
def invoices_file(tmp_path: Path) -> Path:
    """A fixed invoice dataset, so the tests do not drift with the demo data."""
    import json

    payload = {
        "data": [
            {
                "id": "INV-2001", "customer_name": "Logistica Pacifico",
                "customer_email": LOGISTICA, "amount": 98500.0, "currency": "MXN",
                "due_date": "2026-08-03", "status": "pending",
            },
            {
                "id": "INV-2002", "customer_name": "Logistica Pacifico",
                "customer_email": LOGISTICA, "amount": 1200.0, "currency": "MXN",
                "due_date": "2026-09-10", "status": "pending",
            },
            {
                "id": "INV-2003", "customer_name": "Logistica Pacifico",
                "customer_email": LOGISTICA, "amount": 500.0, "currency": "MXN",
                "due_date": "2026-07-01", "status": "paid",
            },
            {
                "id": "INV-3001", "customer_name": "Grupo Meridiano",
                "customer_email": MERIDIANO, "amount": 4780.0, "currency": "MXN",
                "due_date": "2026-08-18", "status": "pending",
            },
            {
                "id": "INV-9999", "customer_name": "Registro Corrupto",
                "customer_email": "x@y.mx", "amount": 1.0, "currency": "MXN",
                "due_date": "2026-13-45", "status": "pending",
            },
        ]
    }
    path = tmp_path / "invoices.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def settings(invoices_file: Path) -> Settings:
    return Settings(
        provider=PROVIDER_FAKE,
        retrieval_backend=BACKEND_MEMORY,
        database_url="postgresql://asistente:asistente@localhost:5432/asistente",
        auth_mode=AUTH_DEMO,
        google_client_id="",
        account_links={},
        session_secret='secreto-de-prueba',
        seed_password='prueba1234',
        azure=AzureSettings("", "", "", "", "2024-10-21"),
        gemini=GeminiSettings("", "gemini-3.6-flash", "gemini-embedding-001"),
        vertex=VertexSettings(None, "", "global", "gemini-2.5-flash", "gemini-embedding-001"),
        corpus_path=ASSISTANT_ROOT / "data" / "polizas",
        invoices_path=invoices_file,
        request_timeout=5.0,
        top_k=4,
        min_similarity=0.15,
        max_question_chars=800,
        overdue_alert_threshold_days=10,
        log_level="INFO",
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def retriever(settings: Settings, provider: FakeProvider) -> Retriever:
    retriever = Retriever(
        load_corpus(settings.corpus_path),
        provider,
        top_k=settings.top_k,
        min_similarity=settings.min_similarity,
    )
    retriever.index()
    return retriever


@pytest.fixture
def assistant(settings: Settings, provider: FakeProvider, retriever: Retriever):
    from app.assistant import Assistant

    return Assistant(
        settings=settings,
        provider=provider,
        retriever=retriever,
        invoice_store=InvoiceStore(settings.invoices_path),
    )


@pytest.fixture
def make_assistant(settings: Settings, retriever: Retriever):
    """Build an assistant around a steered or broken provider."""

    def factory(provider, **overrides):
        from app.assistant import Assistant

        retriever._provider = provider  # noqa: SLF001 - test seam, on purpose
        return Assistant(
            settings=replace(settings, **overrides),
            provider=provider,
            retriever=retriever,
            invoice_store=InvoiceStore(settings.invoices_path),
        )

    return factory
