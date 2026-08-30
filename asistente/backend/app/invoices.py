"""Invoice lookup: the transactional half of the assistant.

Invoices are deliberately **not** in the vector index. They are live data with
per-customer permissions, and indexing them would do two bad things at once:
freeze a number that changes daily, and turn the index into a leak path between
customers. So this intent is answered by querying the system of record and
formatting the result with a template.

The model never writes an amount, a date or a status. It decides *which*
invoice is being asked about; the figures are inserted by code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

MONTHS_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

STATUS_PENDING = "pending"

STATUS_LABELS = {
    "pending": "pendiente de pago",
    "paid": "pagada",
    "cancelled": "cancelada",
}


@dataclass(frozen=True)
class Invoice:
    id: str
    customer_name: str
    customer_email: str
    amount: float
    currency: str
    due_date: date
    status: str


class InvoiceStore:
    """Reads the same dataset the reminder job uses.

    One source of truth for both blocks: what the assistant reports is exactly
    what the job acted on, so the two can never contradict each other.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._invoices: list[Invoice] | None = None

    def load(self) -> list[Invoice]:
        if self._invoices is not None:
            return self._invoices

        payload = json.loads(self._path.read_text(encoding="utf-8"))
        invoices = []
        for record in payload.get("data", []):
            try:
                invoices.append(
                    Invoice(
                        id=str(record["id"]).strip(),
                        customer_name=str(record["customer_name"]).strip(),
                        customer_email=str(record["customer_email"]).strip(),
                        amount=float(record["amount"]),
                        currency=str(record["currency"]).strip(),
                        due_date=date.fromisoformat(str(record["due_date"]).strip()),
                        status=str(record["status"]).strip().lower(),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                # A malformed record is skipped, never fatal: the same rule the
                # reminder job follows.
                logger.warning("Invoice skipped record=%s reason=%s", record.get("id"), exc)

        self._invoices = invoices
        return invoices

    def find(self, invoice_id: str, customer_email: str) -> Invoice | None:
        """Look up one invoice **within the caller's own account**.

        The customer filter is part of the query, not a check afterwards. There
        is no code path that fetches someone else's invoice and then decides
        what to do with it.
        """
        wanted = invoice_id.strip().upper()
        email = customer_email.strip().lower()
        for invoice in self.load():
            if invoice.id.upper() == wanted and invoice.customer_email.lower() == email:
                return invoice
        return None

    def for_customer(self, customer_email: str) -> list[Invoice]:
        email = customer_email.strip().lower()
        return [i for i in self.load() if i.customer_email.lower() == email]


def format_date(value: date) -> str:
    return f"{value.day} de {MONTHS_ES[value.month - 1]} de {value.year}"


def format_amount(invoice: Invoice) -> str:
    return f"${invoice.amount:,.2f} {invoice.currency}"


def invoice_data(invoice: Invoice, today: date, threshold_days: int) -> dict:
    """The same facts as ``describe_invoice``, for an interface to lay out.

    Both come from the same record and neither is derived from the other, so
    the card and the prose cannot drift apart. ``threshold_days`` travels with
    it because "25 días de atraso" means nothing without the line it crossed.
    """
    overdue = (today - invoice.due_date).days
    return {
        "tipo": "factura",
        "id": invoice.id,
        "cliente": invoice.customer_name,
        "importe": round(invoice.amount, 2),
        "importe_texto": format_amount(invoice),
        "moneda": invoice.currency,
        "vencimiento": invoice.due_date.isoformat(),
        "vencimiento_texto": format_date(invoice.due_date),
        "estatus": invoice.status,
        "estatus_texto": STATUS_LABELS.get(invoice.status, invoice.status),
        "dias_atraso": max(overdue, 0),
        "dias_restantes": max(-overdue, 0),
        "vencida": invoice.status == STATUS_PENDING and overdue > 0,
        "umbral_dias": threshold_days,
        "rebasa_umbral": invoice.status == STATUS_PENDING and overdue > threshold_days,
        # La frase que acompaña a la tarjeta. Viene de aquí y no se recorta del
        # texto en el navegador: partir prosa con heurísticas es frágil y se
        # rompe en cuanto alguien reescribe una plantilla.
        "nota": _invoice_note(invoice, overdue),
    }


def _invoice_note(invoice: Invoice, overdue: int) -> str:
    """Qué hacer, que es lo único que la tarjeta no dice con cifras."""
    if invoice.status != STATUS_PENDING:
        return ""
    if overdue > 0:
        return (
            "La factura ya venció. Si el pago ya fue realizado, puede compartirnos "
            "el comprobante para actualizar el estado de su cuenta."
        )
    if overdue == 0:
        return "La factura está vigente y vence hoy."
    return "La factura está vigente y no requiere acción por ahora."


def account_data(invoices: list[Invoice], today: date, threshold_days: int) -> dict:
    """Account summary, structured. Only pending invoices count towards a total."""
    pending = [i for i in invoices if i.status == STATUS_PENDING]
    return {
        "tipo": "cuenta",
        "pendientes": len(pending),
        "total": round(sum(i.amount for i in pending), 2),
        "moneda": pending[0].currency if pending else "",
        "vencidas": sum(1 for i in pending if (today - i.due_date).days > 0),
        "facturas": [invoice_data(i, today, threshold_days) for i in
                     sorted(pending, key=lambda i: i.due_date)],
        "nota": _account_note(pending, today),
    }


def _account_note(pending: list[Invoice], today: date) -> str:
    if not pending:
        return "No tiene facturas pendientes de pago en este momento."
    vencidas = sum(1 for i in pending if (today - i.due_date).days > 0)
    if not vencidas:
        return "Ninguna de sus facturas pendientes ha vencido todavía."
    return (
        f"{_count(vencidas, 'factura está vencida', 'facturas están vencidas')} "
        "y requieren atención."
    )


def describe_invoice(invoice: Invoice, today: date) -> str:
    """Deterministic answer for one invoice. No model involved."""
    status = STATUS_LABELS.get(invoice.status, invoice.status)
    lines = [
        f"La factura {invoice.id} está {status}.",
        "",
        f"  Importe          {format_amount(invoice)}",
        f"  Vencimiento      {format_date(invoice.due_date)}",
        f"  Estatus          {status}",
    ]

    if invoice.status == "pending":
        overdue = (today - invoice.due_date).days
        if overdue > 0:
            dias = "1 día" if overdue == 1 else f"{overdue} días"
            lines.append(f"  Atraso           {dias}")
            lines.append("")
            lines.append(
                "La factura ya venció. Si el pago ya fue realizado, puede compartirnos "
                "el comprobante para actualizar el estado de su cuenta."
            )
        else:
            faltan = -overdue
            plazo = "hoy" if faltan == 0 else (
                "mañana" if faltan == 1 else f"en {faltan} días"
            )
            lines.append("")
            lines.append(f"La factura está vigente y vence {plazo}.")

    return "\n".join(lines)


def describe_account(invoices: list[Invoice], today: date) -> str:
    """Deterministic summary when no specific invoice was named."""
    pending = [i for i in invoices if i.status == "pending"]
    if not pending:
        return "No tiene facturas pendientes de pago en este momento."

    overdue = [i for i in pending if (today - i.due_date).days > 0]
    total = sum(i.amount for i in pending)
    currency = pending[0].currency

    lines = [
        f"Tiene {_count(len(pending), 'factura pendiente', 'facturas pendientes')} "
        f"por un total de ${total:,.2f} {currency}.",
        "",
    ]
    for invoice in sorted(pending, key=lambda i: i.due_date):
        dias = (today - invoice.due_date).days
        marca = f"vencida hace {dias} d." if dias > 0 else "vigente"
        lines.append(
            f"  {invoice.id}   {format_amount(invoice):>18}   "
            f"vence {format_date(invoice.due_date)}   ({marca})"
        )
    if overdue:
        lines.append("")
        lines.append(
            f"{_count(len(overdue), 'factura está vencida', 'facturas están vencidas')} "
            "y requieren atención."
        )
    return "\n".join(lines)


def _count(number: int, singular: str, plural: str) -> str:
    return f"1 {singular}" if number == 1 else f"{number} {plural}"
