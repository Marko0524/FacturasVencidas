"""Regenerate sample_data/invoices.json with due dates relative to today.

Fixed dates rot: an invoice that is "exactly 10 days overdue" today is 11 days
overdue tomorrow, and the dataset stops demonstrating the rule it was built for.
Run this script to refresh the fixtures before a demo.

Usage:
    python scripts/refresh_sample_data.py
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "sample_data" / "invoices.json"

# ``overdue_days`` is positive for overdue invoices and negative for future ones.
# ``due_date`` overrides it, and is used to inject a deliberately broken record.
TEMPLATES: list[dict] = [
    {
        "id": "INV-1001",
        "customer_name": "Comercial Aurora",
        "customer_email": "pagos@aurora.mx",
        "amount": 8200.00,
        "currency": "MXN",
        "status": "paid",
        "overdue_days": 20,
    },
    {
        "id": "INV-1002",
        "customer_name": "Distribuidora del Norte",
        "customer_email": "cuentas@delnorte.mx",
        "amount": 23400.75,
        "currency": "MXN",
        "status": "pending",
        "overdue_days": -7,
    },
    {
        "id": "INV-1003",
        "customer_name": "Empresa Demo",
        "customer_email": "cliente@empresa.com",
        "amount": 15000.50,
        "currency": "MXN",
        "status": "pending",
        "overdue_days": 5,
    },
    {
        "id": "INV-1004",
        "customer_name": "Grupo Meridiano",
        "customer_email": "finanzas@meridiano.mx",
        "amount": 4780.00,
        "currency": "MXN",
        "status": "pending",
        "overdue_days": 10,
    },
    {
        "id": "INV-1005",
        "customer_name": "Textiles del Bajio",
        "customer_email": "tesoreria@textilesbajio.mx",
        "amount": 61250.00,
        "currency": "MXN",
        "status": "pending",
        "overdue_days": 15,
    },
    {
        "id": "INV-1006",
        "customer_name": "Servicios Integrales Lopez",
        "customer_email": "admin@silopez.mx",
        "amount": 3120.30,
        "currency": "MXN",
        "status": "pending",
        "overdue_days": 3,
    },
    {
        "id": "INV-1007",
        "customer_name": "Logistica Pacifico",
        "customer_email": "pagos@logpacifico.mx",
        "amount": 98500.00,
        "currency": "MXN",
        "status": "pending",
        "overdue_days": 25,
    },
    {
        "id": "INV-1008",
        "customer_name": "Constructora Zenit",
        "customer_email": "contabilidad@zenit.mx",
        "amount": 17300.00,
        "currency": "MXN",
        "status": "cancelled",
        "overdue_days": 40,
    },
    {
        "id": "INV-9999",
        "customer_name": "Registro Corrupto",
        "customer_email": "soporte@empresa.com",
        "amount": 999.99,
        "currency": "MXN",
        "status": "pending",
        "due_date": "2026-13-45",
    },
]


def build_invoices(today: date) -> list[dict]:
    """Materialise the templates into API-shaped invoice records."""
    invoices = []
    for template in TEMPLATES:
        record = {key: value for key, value in template.items() if key != "overdue_days"}
        if "due_date" not in record:
            record["due_date"] = (today - timedelta(days=template["overdue_days"])).isoformat()
        invoices.append(
            {
                "id": record["id"],
                "customer_name": record["customer_name"],
                "customer_email": record["customer_email"],
                "amount": record["amount"],
                "currency": record["currency"],
                "due_date": record["due_date"],
                "status": record["status"],
            }
        )
    return invoices


def main() -> None:
    today = date.today()
    payload = {"data": build_invoices(today)}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['data'])} invoices to {OUTPUT_PATH} (reference date {today.isoformat()})")


if __name__ == "__main__":
    main()
