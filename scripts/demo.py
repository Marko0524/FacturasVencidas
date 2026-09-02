"""Reproduce la secuencia de la demostración en un solo comando.

Corre desde la raíz del proyecto, en Windows, Linux o macOS:

    python scripts/demo.py

Pasos, en orden:

  1. Refresca el dataset para que los días de atraso sean los que documenta el README.
  2. Corrida 1 con estado limpio: 8 recordatorios + 4 alertas (simulados en log).
  3. Corrida 2 inmediata: skipped=12, ninguna notificación repetida (idempotencia).
  4. Corrida con DRY_RUN=true sobre un estado vacio: muestra el flujo completo y
     comprueba que no se persiste nada.
  5. Corrida con la API inalcanzable: reintentos con backoff y código de salida 1.

Usa un archivo de estado propio (state/demo-notifications.json) para no pisar el
estado real de quien ya haya ejecutado main.py, y lo borra al terminar.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / "state" / "demo-notifications.json"
DRY_RUN_STATE_FILE = PROJECT_ROOT / "state" / "demo-dry-run.json"


def paso(titulo: str, env_extra: dict[str, str], esperado: str) -> None:
    print()
    print("=" * 78)
    print(f"  {titulo}")
    print(f"  esperado: {esperado}")
    print("=" * 78)
    env = {**os.environ, "STATE_FILE_PATH": str(STATE_FILE), **env_extra}
    resultado = subprocess.run(
        [sys.executable, "main.py"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    print(f"  exit code = {resultado.returncode}")


def main() -> int:
    if STATE_FILE.exists():
        STATE_FILE.unlink()

    print("Refrescando sample_data/invoices.json con fechas relativas a hoy...")
    subprocess.run(
        [sys.executable, "scripts/refresh_sample_data.py"],
        cwd=PROJECT_ROOT,
        check=True,
    )

    paso(
        "1/4  Corrida con estado limpio",
        {},
        "fetched=16 invalid=1 overdue=8 reminders=8 alerts=4 skipped=0 errors=0",
    )
    paso(
        "2/4  Segunda corrida, mismo dia: idempotencia",
        {},
        "reminders=0 alerts=0 skipped=12 errors=0  (nada se reenvia)",
    )
    paso(
        "3/4  DRY_RUN=true con estado vacio: muestra el flujo completo sin persistirlo",
        {"DRY_RUN": "true", "STATE_FILE_PATH": str(DRY_RUN_STATE_FILE)},
        "reminders=8 alerts=4, y el archivo de estado NO se crea",
    )
    creado = DRY_RUN_STATE_FILE.exists()
    print(f"  archivo de estado creado por DRY_RUN: {'SI (inesperado)' if creado else 'no'}")
    paso(
        "4/4  API inalcanzable: reintentos con backoff y salida distinta de cero",
        {
            "INVOICES_API_URL": "http://127.0.0.1:9",  # puerto discard: nadie escucha
            "MAX_RETRIES": "2",
            "RETRY_BACKOFF_BASE": "0.2",
            "REQUEST_TIMEOUT": "2",
        },
        "3 intentos (1 + 2 reintentos), 'Process failed reason=api_error', exit code = 1",
    )

    for archivo in (STATE_FILE, DRY_RUN_STATE_FILE):
        if archivo.exists():
            archivo.unlink()
    print()
    print("Demo terminada. El estado de la demo se elimino; el tuyo no se toco.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
