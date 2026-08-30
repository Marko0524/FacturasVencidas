"""Las sugerencias de la pantalla de bienvenida.

Función pura: no toca base ni modelo. Lo que se fija aquí es que salgan del
expediente de la cuenta y no de una lista escrita a mano. Medido contra el
sistema en marcha antes de este cambio: tres de las seis sugerencias fijas
escalaban para Comercial Aurora, y la primera —la factura INV-1007— no era de
ninguna cuenta que pudiera entrar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.sugerencias import GENERALES, MAXIMO, para_cliente


@dataclass(frozen=True)
class Factura:
    id: str
    due_date: date
    status: str = "pending"


@dataclass(frozen=True)
class Documento:
    titulo: str


HOY = date(2026, 8, 28)


def test_the_suggested_invoice_is_one_the_customer_actually_has():
    """Lo que fallaba: la sugerencia nombraba una factura de nadie."""
    facturas = [Factura("INV-2001", date(2026, 8, 25)), Factura("INV-2002", date(2026, 9, 10))]

    assert any("INV-2001" in s for s in para_cliente(facturas, []))


def test_the_most_urgent_invoice_comes_first():
    """La que vence antes es la que la persona venía a mirar."""
    facturas = [Factura("INV-2002", date(2026, 9, 10)), Factura("INV-2001", date(2026, 8, 25))]

    assert "INV-2001" in para_cliente(facturas, [])[0]


def test_a_paid_invoice_is_not_the_headline():
    """Preguntar por una ya pagada es una sugerencia sin urgencia."""
    facturas = [
        Factura("INV-1000", date(2026, 1, 1), status="paid"),
        Factura("INV-2001", date(2026, 8, 25), status="pending"),
    ]

    assert "INV-2001" in para_cliente(facturas, [])[0]


def test_documents_can_be_summarised_by_their_real_title():
    sugerencias = para_cliente([], [Documento("Carátula de póliza — Aurora")])

    assert any("Carátula de póliza — Aurora" in s for s in sugerencias)


def test_an_account_with_nothing_still_gets_something_answerable():
    """Se apoyan en el corpus público, que toda cuenta tiene autorizado."""
    sugerencias = para_cliente([], [])

    assert sugerencias == GENERALES


def test_nothing_is_suggested_that_was_not_handed_in():
    """Lo que entra ya pasó el filtro de permisos, así que no se puede sugerir
    —ni por tanto revelar— nada de otra cuenta."""
    sugerencias = para_cliente([Factura("INV-2001", HOY)], [Documento("Anexo de Aurora")])

    assert not any("INV-3001" in s for s in sugerencias)
    assert not any("Meridiano" in s for s in sugerencias)


def test_the_list_is_short_enough_to_be_read():
    facturas = [Factura(f"INV-{n}", HOY) for n in range(2000, 2010)]
    documentos = [Documento(f"Documento {n}") for n in range(10)]

    assert len(para_cliente(facturas, documentos)) <= MAXIMO


def test_there_are_no_repeats():
    sugerencias = para_cliente([Factura("INV-2001", HOY)], [Documento("Uno"), Documento("Uno")])

    assert len(sugerencias) == len(set(sugerencias))
