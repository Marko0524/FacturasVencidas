"""Invoice lookup and the deterministic templates that report it."""

from __future__ import annotations

from datetime import date

from app.invoices import InvoiceStore, describe_account, describe_invoice, format_date
from tests.conftest import AURORA, LOGISTICA, MERIDIANO

TODAY = date(2026, 8, 28)


def test_a_customer_only_finds_their_own_invoice(invoices_file):
    store = InvoiceStore(invoices_file)

    assert store.find("INV-2001", LOGISTICA) is not None
    assert store.find("INV-3001", LOGISTICA) is None


def test_the_lookup_is_case_insensitive_on_both_sides(invoices_file):
    store = InvoiceStore(invoices_file)

    assert store.find("inv-2001", LOGISTICA.upper()) is not None


def test_a_malformed_record_is_skipped_not_fatal(invoices_file):
    """Same rule as the reminder job: one bad row must not sink the batch."""
    store = InvoiceStore(invoices_file)

    ids = {invoice.id for invoice in store.load()}
    assert "INV-9999" not in ids
    assert "INV-2001" in ids


def test_an_account_with_no_invoices_comes_back_empty(invoices_file):
    assert InvoiceStore(invoices_file).for_customer(AURORA) == []


# --- deterministic wording ---------------------------------------------------


def test_an_overdue_invoice_reports_the_days_and_the_figures(invoices_file):
    invoice = InvoiceStore(invoices_file).find("INV-2001", LOGISTICA)

    text = describe_invoice(invoice, TODAY)

    assert "$98,500.00 MXN" in text
    assert "3 de agosto de 2026" in text
    assert "25 días" in text
    assert "pendiente de pago" in text


def test_a_current_invoice_is_not_reported_as_overdue(invoices_file):
    invoice = InvoiceStore(invoices_file).find("INV-2002", LOGISTICA)

    text = describe_invoice(invoice, TODAY)

    assert "vigente" in text
    assert "Atraso" not in text


def test_a_paid_invoice_reports_as_paid(invoices_file):
    invoice = InvoiceStore(invoices_file).find("INV-2003", LOGISTICA)

    assert "pagada" in describe_invoice(invoice, TODAY)


def test_one_day_overdue_is_not_pluralised(invoices_file):
    invoice = InvoiceStore(invoices_file).find("INV-2001", LOGISTICA)

    text = describe_invoice(invoice, date(2026, 8, 4))

    assert "1 día" in text
    assert "1 días" not in text


def test_the_account_summary_totals_only_pending_invoices(invoices_file):
    invoices = InvoiceStore(invoices_file).for_customer(LOGISTICA)

    text = describe_account(invoices, TODAY)

    # 98,500.00 + 1,200.00 -- the paid one must not be counted.
    assert "$99,700.00 MXN" in text
    assert "INV-2003" not in text


def test_an_account_with_nothing_pending_says_so(invoices_file):
    invoices = InvoiceStore(invoices_file).for_customer(MERIDIANO)
    paid = [invoice for invoice in invoices if invoice.status == "paid"]

    assert "No tiene facturas pendientes" in describe_account(paid, TODAY)


def test_dates_are_written_in_spanish_without_touching_the_locale():
    assert format_date(date(2026, 1, 9)) == "9 de enero de 2026"
    assert format_date(date(2026, 12, 31)) == "31 de diciembre de 2026"
