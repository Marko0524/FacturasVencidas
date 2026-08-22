# Invoice Reminder — Recordatorio de Pagos

Automatiza el seguimiento de facturas vencidas: consulta las facturas, aplica las reglas de negocio, notifica al cliente y escala a Operaciones cuando el atraso lo amerita — sin duplicar notificaciones si el proceso se vuelve a ejecutar.

---

## 1. Descripción

El problema: las facturas vencidas se detectan tarde y el seguimiento manual es inconsistente. Un operador revisa un reporte, decide a quién escribirle y con frecuencia envía el mismo recordatorio dos veces o no lo envía nunca.

Esta aplicación resuelve ese ciclo como un **job desatendido**:

| Situación | Acción |
|---|---|
| Factura pagada o cancelada | Ninguna |
| Factura vigente (vence hoy o después) | Ninguna |
| 1 a 10 días de atraso | Recordatorio al cliente |
| Más de 10 días de atraso | Recordatorio al cliente **+ alerta a Operaciones** |
| Registro con datos inválidos | Se descarta con `WARNING`, el resto continúa |

El umbral de 10 días es configurable (`OVERDUE_ALERT_THRESHOLD_DAYS`). El envío de correo se **simula con `logging`**; no se conecta a ningún proveedor real.

---

## 2. Arquitectura

```text
API REST  /  JSON local
          ↓
      api_client.py        timeout, reintentos selectivos, backoff con jitter
          ↓
   invoice_service.py      parseo + validación + reglas de negocio (funciones puras)
          ↓
    idempotency.py         ¿esta notificación ya se envió hoy?   ← aquí interviene
          ↓
      notifier.py          simulación de envío
          ↓
   Cliente  /  Operaciones
```

Responsabilidades:

| Módulo | Responsabilidad | Efectos secundarios |
|---|---|---|
| `app/config.py` | Único lector de variables de entorno; valida y aplica defaults | Ninguno |
| `app/api_client.py` | Obtiene los registros crudos (API o archivo), reintentos y backoff | Red / disco |
| `app/invoice_service.py` | Valida registros y decide qué notificar | **Ninguno — funciones puras** |
| `app/idempotency.py` | Estado de notificaciones ya enviadas | Disco (escritura atómica) |
| `app/notifier.py` | Entrega de notificaciones | Log |
| `main.py` | Orquestación, contadores y código de salida | Log |

**Dónde interviene la idempotencia:** entre la decisión y el envío. `invoice_service` decide *qué* debería enviarse; `idempotency` decide *si realmente hace falta enviarlo*. Por eso la garantía cubre la **acción**, no la lectura de la API: releer las facturas mil veces es inofensivo, enviar el mismo correo mil veces no.

---

## 3. Configuración

Todo se lee de variables de entorno. **No hay ningún secreto en el código ni en la imagen.**

| Variable | Default | Descripción |
|---|---|---|
| `INVOICES_API_URL` | *(vacío)* | URL de la API. **Vacía → se lee el JSON local** |
| `API_TOKEN` | *(vacío)* | Se envía como `Authorization: Bearer <token>`. Vacío → sin cabecera |
| `OPERATIONS_EMAIL` | `operaciones@empresa.com` | Destinatario de las alertas |
| `REQUEST_TIMEOUT` | `10` | Timeout HTTP en segundos |
| `MAX_RETRIES` | `3` | Reintentos adicionales al intento inicial |
| `RETRY_BACKOFF_BASE` | `0.5` | Base del backoff exponencial, en segundos |
| `OVERDUE_ALERT_THRESHOLD_DAYS` | `10` | Días de atraso a partir de los cuales se alerta a Operaciones |
| `STATE_FILE_PATH` | `./state/notifications.json` | Estado de idempotencia |
| `SAMPLE_DATA_PATH` | `./sample_data/invoices.json` | Dataset local |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `DRY_RUN` | `false` | `true` simula todo pero **no persiste** el estado |

Las rutas relativas se resuelven contra la raíz del proyecto, así que la app funciona desde cualquier directorio de trabajo.

Plantilla: `.env.example`. **Los defaults bastan para ejecutar sin `.env` y sin red.**

---

## 4. Ejecución local

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux / macOS

pip install -r requirements.txt
python main.py
```

Esto ya funciona: sin `.env`, sin red y sin configuración previa, leyendo `sample_data/invoices.json`.

Para apuntar a una API real:

```bash
cp .env.example .env     # y edita INVOICES_API_URL / API_TOKEN
```

**Refrescar el dataset** (las fechas son relativas al día de ejecución):

```bash
python scripts/refresh_sample_data.py
```

**Volver a demostrar el flujo completo** sin borrar el estado:

```bash
DRY_RUN=true python main.py       # Linux / macOS
$env:DRY_RUN="true"; python main.py   # PowerShell
```

---

## 5. Docker

```bash
docker build -t invoice-reminder .
docker run --env-file .env -v "$(pwd)/state:/app/state" invoice-reminder
```

**El volumen no es opcional.** El estado de idempotencia vive en un archivo dentro del contenedor, y el sistema de archivos de un contenedor es efímero: sin `-v`, cada `docker run` arranca con estado vacío y reenvía todas las notificaciones. Montar `state/` es lo que hace que la garantía sobreviva entre ejecuciones. Es también la razón por la que en producción este archivo se sustituye por un almacenamiento externo (ver §7).

Comprobado con Docker 29.7.2:

```text
# CON volumen -v ./state:/app/state
run 1 -> fetched=9 invalid=1 overdue=5 reminders=5 alerts=2 skipped=0 errors=0
run 2 -> fetched=9 invalid=1 overdue=5 reminders=0 alerts=0 skipped=7 errors=0   <- idempotente

# SIN volumen
run 1 -> fetched=9 invalid=1 overdue=5 reminders=5 alerts=2 skipped=0 errors=0
run 2 -> fetched=9 invalid=1 overdue=5 reminders=5 alerts=2 skipped=0 errors=0   <- reenvía todo
```

La imagen corre como usuario `appuser` (uid 1001), no como root, y no contiene `.env`, `tests/` ni `scripts/`:

```console
$ docker run --rm --entrypoint sh invoice-reminder -c "id; ls /app"
uid=1001(appuser) gid=1001(appuser) groups=1001(appuser)
app  main.py  requirements.txt  sample_data  state
```

---

## 6. Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

50 pruebas, todas deterministas: `today` se inyecta siempre, no hay llamadas de red y no se duerme de verdad (`sleep` es inyectable).

| Archivo | Cubre |
|---|---|
| `test_invoice_service.py` | Reglas de negocio, bordes 10/11 días, estados, parseo y registros corruptos |
| `test_idempotency.py` | Store, escritura atómica, archivo corrupto, `DRY_RUN`, y **segunda ejecución completa → 0 notificaciones** |
| `test_api_client.py` | Qué se reintenta y qué no, `Retry-After`, crecimiento del backoff, cabecera Bearer, fuente de archivo |

---

## 7. Manejo de errores e idempotencia

### Reintentos: selectivos, no indiscriminados

| Situación | Decisión | Razón |
|---|---|---|
| `429`, `500`, `502`, `503`, `504` | **Reintentar** | Fallo transitorio; el mismo request puede funcionar en un segundo |
| `ConnectionError`, `Timeout` | **Reintentar** | Fallo de red, DNS o latencia; no es culpa del request |
| `400`, `401`, `403`, `404` | **No reintentar** | El request está mal o no está autorizado; reintentar solo quema tiempo y cuota |
| Payload inválido | **No reintentar** | Error de contrato, no de disponibilidad |

Backoff exponencial con **full jitter** (`base·2^n + rand(0, base·2^n)`, tope de 30 s). El jitter evita que varios clientes reintenten sincronizados y vuelvan a tumbar el servicio. En `429` se respeta la cabecera `Retry-After` cuando viene en segundos.

**Implementación:** bucle explícito en lugar de `urllib3.Retry`. Es más código, pero permite respetar `Retry-After`, loguear cada reintento con su causa y su espera, y — sobre todo — es directamente auditable en una revisión. `urllib3.Retry` habría sido más corto y menos explicable.

### Idempotencia

**Clave elegida:** `invoice_id | notification_type | run_date`

```text
INV-1005|reminder|2026-08-21
INV-1005|operations_alert|2026-08-21
```

**Política resultante:** como máximo **una notificación de cada tipo, por factura, por día**.

Por qué esta y no otra:

- La clave sugerida habitualmente, `invoice_id + type + days_overdue`, produce el mismo comportamiento **solo si el job corre una vez al día**. Como `days_overdue` cambia cada día, la clave también cambia y se re-notifica a diario — pero eso es un efecto lateral, no una decisión. Poner `run_date` explícitamente hace que la intención sea legible y que el comportamiento no dependa de la frecuencia del scheduler.
- Una clave sin componente temporal (`invoice_id + type`) enviaría **un solo recordatorio en la vida de la factura**. Para una factura con 60 días de atraso eso es inútil: la cobranza necesita insistencia.
- El tipo forma parte de la clave para que el recordatorio y la alerta se rastreen por separado: si la alerta a Operaciones falla, el recordatorio ya enviado no se repite.

**Cuándo se marca la clave: después del envío.** Es una elección deliberada de *at-least-once*:

| Estrategia | Si el proceso muere entre envío y marca | Si muere entre marca y envío |
|---|---|---|
| Marcar **después** (la elegida) | Se reenvía → cliente recibe un duplicado | — |
| Marcar **antes** | — | Notificación **perdida**, nadie se entera |

Para cobranza, un recordatorio duplicado es una molestia; uno perdido es dinero que no se cobra. Se prefiere el duplicado. En un sistema con envío real y costo por mensaje, la decisión podría invertirse, o resolverse con una marca en dos fases (`pending` → `sent`).

**Robustez del estado:**

- Escritura **atómica** (archivo temporal + `os.replace`): un corte a media escritura no corrompe el estado.
- Escritura *write-through* en cada notificación: si el proceso muere a mitad del lote, lo ya enviado queda registrado. Con lotes pequeños el costo es despreciable; a gran escala sería una escritura por lote o un store real.
- Un archivo de estado **corrupto o ilegible no detiene el proceso**: se registra `WARNING` y se arranca desde vacío. En el peor caso se duplica, nunca se cae.

**En producción**, el archivo JSON se reemplaza por:

| Opción | Cuándo |
|---|---|
| **Azure Table Storage** | Recomendada aquí: clave/valor, baratísima, y la clave de idempotencia mapea 1:1 a PartitionKey/RowKey |
| **Cosmos DB** | Si además se necesita consulta, TTL automático o distribución global |
| **Redis** | Si el volumen es alto y basta con expiración por TTL |
| **SQL** | Si la auditoría de notificaciones debe convivir con el resto del modelo transaccional |

La interfaz de `NotificationStore` (`was_processed` / `mark_processed`) está pensada para ese cambio: es lo único que habría que reimplementar.

### Errores en el procesamiento

- Un registro con datos inválidos se aísla y se reporta; **nunca aborta el lote**.
- Un fallo al enviar una notificación se cuenta y se registra; el resto del lote continúa.
- Código de salida: `0` en éxito, `1` si la API agotó los reintentos o si alguna notificación falló. En un job programado, el exit code es la señal que consume el orquestador.

---

## 8. Consideraciones de seguridad

- **Ningún secreto en el código.** No hay tokens, contraseñas ni URLs internas escritos en los fuentes.
- **Todo por variables de entorno**, centralizadas en `app/config.py`. Ningún otro módulo llama a `os.getenv()`, así que la superficie de configuración es auditable de un vistazo.
- **`.env` está en `.gitignore` y en `.dockerignore`.** Solo se versiona la plantilla, sin valores reales.
- **La imagen no contiene secretos**: se inyectan en tiempo de ejecución con `--env-file`. No hay `ENV API_TOKEN=` ni `COPY .env`.
- **Los logs no exponen información sensible**: nunca se registra el token ni la cabecera `Authorization`. Se registran identificadores de factura, correos de destino e importes, que son los datos mínimos necesarios para operar y auditar el proceso.
- **La imagen corre como usuario no privilegiado** (uid 1001).
- **En producción: Azure Key Vault.** El token se guarda en Key Vault y se accede con **Managed Identity**, sin credenciales en configuración de la aplicación. Rotarlo pasa a ser una operación de plataforma, no un redeploy.

---

## 9. Salida de ejemplo

Ejecución real, dataset del repositorio, fecha de referencia 2026-08-21.

**Primera ejecución:**

```text
2026-08-21 17:35:32 INFO     Process started source=file run_date=2026-08-21 threshold_days=10 dry_run=False
2026-08-21 17:35:32 INFO     Reading invoices from local file path=...\sample_data\invoices.json
2026-08-21 17:35:32 INFO     Invoices fetched count=9
2026-08-21 17:35:32 WARNING  Invoice discarded invoice=INV-9999 reason=invalid_due_date
2026-08-21 17:35:32 INFO     No previous notification state found path=...\state\notifications.json
2026-08-21 17:35:32 INFO     Invoice is overdue invoice=INV-1003 days_overdue=5 due_date=2026-08-16
2026-08-21 17:35:32 INFO     Payment reminder sent to=cliente@empresa.com invoice=INV-1003 customer="Empresa Demo" amount=15000.50 currency=MXN due_date=2026-08-16
2026-08-21 17:35:32 INFO     Invoice is overdue invoice=INV-1004 days_overdue=10 due_date=2026-08-11
2026-08-21 17:35:32 INFO     Payment reminder sent to=finanzas@meridiano.mx invoice=INV-1004 customer="Grupo Meridiano" amount=4780.00 currency=MXN due_date=2026-08-11
2026-08-21 17:35:32 INFO     Invoice is overdue invoice=INV-1005 days_overdue=15 due_date=2026-08-06
2026-08-21 17:35:32 INFO     Payment reminder sent to=tesoreria@textilesbajio.mx invoice=INV-1005 customer="Textiles del Bajio" amount=61250.00 currency=MXN due_date=2026-08-06
2026-08-21 17:35:32 WARNING  Operations alert sent to=operaciones@empresa.com invoice=INV-1005 customer="Textiles del Bajio" days_overdue=15 amount=61250.00 currency=MXN
2026-08-21 17:35:32 INFO     Invoice is overdue invoice=INV-1006 days_overdue=3 due_date=2026-08-18
2026-08-21 17:35:32 INFO     Payment reminder sent to=admin@silopez.mx invoice=INV-1006 customer="Servicios Integrales Lopez" amount=3120.30 currency=MXN due_date=2026-08-18
2026-08-21 17:35:32 INFO     Invoice is overdue invoice=INV-1007 days_overdue=25 due_date=2026-07-27
2026-08-21 17:35:32 INFO     Payment reminder sent to=pagos@logpacifico.mx invoice=INV-1007 customer="Logistica Pacifico" amount=98500.00 currency=MXN due_date=2026-07-27
2026-08-21 17:35:32 WARNING  Operations alert sent to=operaciones@empresa.com invoice=INV-1007 customer="Logistica Pacifico" days_overdue=25 amount=98500.00 currency=MXN
2026-08-21 17:35:32 INFO     Process finished fetched=9 invalid=1 overdue=5 reminders=5 alerts=2 skipped=0 errors=0
```

**Segunda ejecución, inmediatamente después — la idempotencia en acción:**

```text
2026-08-21 17:35:36 INFO     Process started source=file run_date=2026-08-21 threshold_days=10 dry_run=False
2026-08-21 17:35:36 INFO     Invoices fetched count=9
2026-08-21 17:35:36 WARNING  Invoice discarded invoice=INV-9999 reason=invalid_due_date
2026-08-21 17:35:36 INFO     Notification state loaded path=...\state\notifications.json entries=7
2026-08-21 17:35:36 INFO     Invoice is overdue invoice=INV-1003 days_overdue=5 due_date=2026-08-16
2026-08-21 17:35:36 INFO     Notification skipped reason=already_processed invoice=INV-1003 type=reminder
2026-08-21 17:35:36 INFO     Invoice is overdue invoice=INV-1005 days_overdue=15 due_date=2026-08-06
2026-08-21 17:35:36 INFO     Notification skipped reason=already_processed invoice=INV-1005 type=reminder
2026-08-21 17:35:36 INFO     Notification skipped reason=already_processed invoice=INV-1005 type=operations_alert
2026-08-21 17:35:36 INFO     Process finished fetched=9 invalid=1 overdue=5 reminders=0 alerts=0 skipped=7 errors=0
```

Mismas 5 facturas vencidas, **0 notificaciones enviadas, 7 omitidas**.

### El dataset

9 facturas que cubren todas las ramas de decisión:

| Factura | Estado | Atraso | Resultado esperado |
|---|---|---|---|
| INV-1001 | `paid` | 20 días | Ninguna acción |
| INV-1002 | `pending` | −7 (vigente) | Ninguna acción |
| INV-1003 | `pending` | 5 días | Recordatorio |
| INV-1004 | `pending` | **10 días (borde)** | Recordatorio, **sin** alerta |
| INV-1005 | `pending` | 15 días | Recordatorio + alerta |
| INV-1006 | `pending` | 3 días | Recordatorio |
| INV-1007 | `pending` | 25 días | Recordatorio + alerta |
| INV-1008 | `cancelled` | 40 días | Ninguna acción |
| INV-9999 | `pending` | fecha inválida | Descartada con `WARNING` |

---

## 10. Deployment in Azure

### Opción A — Azure Functions (Timer Trigger)

```text
Timer Trigger  →  Azure Function  →  Invoices API  →  Business Logic  →  Notifications
   (0 0 8 * * *)                            ↓
                                    Azure Table Storage
                                     (idempotencia)
```

Ejecución programada, por ejemplo una vez al día a las 08:00, con un Timer Trigger. El código de `app/` se reutiliza tal cual; solo cambia el punto de entrada.

Servicios de apoyo:

- **Managed Identity** — la Function se autentica contra Key Vault y Storage sin credenciales en configuración.
- **Azure Key Vault** — custodia `API_TOKEN`; se referencia desde App Settings con `@Microsoft.KeyVault(...)`.
- **Application Insights** — los logs `clave=valor` ya emitidos se consultan con KQL; los contadores del resumen final se convierten directamente en métricas y alertas ("0 recordatorios enviados hoy" es un síntoma).
- **Azure Table Storage o Cosmos DB** — reemplazo del archivo JSON de idempotencia.

### Opción B — Azure Container Apps Job

```text
Azure Container Registry  →  Azure Container Apps Job  (scheduled)
```

El mismo `Dockerfile` de este repositorio se publica en ACR y se ejecuta como Job programado, con la misma expresión cron. Sin cambios en el código y sin modelo de programación específico del proveedor.

### Comparación y recomendación

| | Azure Functions | Container Apps Job |
|---|---|---|
| Empaquetado | Código + runtime del proveedor | Imagen Docker (portable) |
| Arranque | Consumption puede tener cold start | Arranque de contenedor |
| Costo en reposo | Cercano a cero | Cercano a cero |
| Acoplamiento | Alto — modelo de Functions | Bajo — corre igual en cualquier lado |
| Ecosistema | Bindings nativos, integración inmediata | Genérico |
| Recursos | Límites del plan | CPU/memoria a medida |

**Recomendación para este escenario: Azure Functions con Timer Trigger.** El proceso es corto, ligero, sin estado propio y de ejecución diaria — el caso de uso exacto de Functions. La integración nativa con Key Vault, Managed Identity y Application Insights se obtiene prácticamente gratis, y no hay que mantener un registry ni un pipeline de imágenes.

Elegiría **Container Apps Job** si el proceso creciera en dependencias del sistema, necesitara tiempos de ejecución largos, o si la organización ya estandarizó en contenedores y quiere un único mecanismo de despliegue. Como el `Dockerfile` ya existe y la lógica no depende de ninguna API de Azure, migrar de una opción a la otra es cuestión de horas, no de semanas.

---

## 11. Limitaciones y siguientes pasos

Lo que quedó fuera a propósito por el alcance de la prueba:

- **El archivo de estado crece sin límite.** No hay purga de claves antiguas. En producción se resuelve con TTL (Cosmos DB, Redis) o con un job de limpieza.
- **El envío es simulado.** No hay reintentos ni cola de mensajes fallidos; con un proveedor real haría falta una dead-letter queue.
- **Sin concurrencia.** Dos instancias simultáneas pueden duplicar notificaciones, porque el archivo local no da exclusión mutua. Un store real con escritura condicional (ETag en Table Storage, `SETNX` en Redis) lo resuelve.
- **`Retry-After` solo en formato de segundos**; la variante con fecha HTTP cae al backoff normal.
- **Sin paginación** en el cliente de API, conforme al contrato definido.
- **Fechas naive en hora local.** Con clientes en varias zonas horarias habría que fijar la zona de negocio explícitamente, porque "vencida" es una afirmación relativa a un huso.
