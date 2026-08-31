# Invoice Reminder — Recordatorio de Pagos

Automatiza el seguimiento de facturas vencidas: consulta las facturas, aplica las reglas de negocio, notifica al cliente y escala a Operaciones cuando el atraso lo amerita — sin duplicar notificaciones si el proceso se vuelve a ejecutar.

**[Documento de aterrizaje del requerimiento](docs/Bloque%201%20-%20Aterrizaje%20de%20Requerimiento.docx)** — el alcance acordado, los supuestos y las preguntas de las que salen las reglas de abajo.

**[Ver la demostración en video](https://drive.google.com/file/d/1H_lIz7a0iXisAuRdzHDJFYBie3V7Glyq/view?usp=sharing)** — la solución en ejecución: reglas de negocio, envío real de correo, idempotencia, manejo de errores y el contenedor.

El dominio es común a todos los bloques: **LeaseMD** administra pólizas y factura a clientes, y Cobranza y Operaciones consumen estas automatizaciones. Por eso el job, el asistente y las consultas SQL hablan de las mismas entidades — facturas, atraso, escalamiento a Operaciones — en lugar de ser ejercicios independientes.

### Mapa del entregable

| Bloque | Objetivo | Herramientas | Dónde está |
|---|---|---|---|
| **1** · Aterrizaje | Convertir un requerimiento ambiguo en un alcance comprometible | Documentación de ingeniería | §11 · [documento](docs/Bloque%201%20-%20Aterrizaje%20de%20Requerimiento.docx) |
| **2** · Automatización | Recordatorios de pago y escalamiento a Operaciones | Python, `requests`, pytest, Docker | §1–§10 · `app/`, `main.py`, `tests/` |
| **3** · Asistente de IA | Responder dudas de pólizas y facturación con gobierno de datos | Azure OpenAI / Vertex AI / Gemini, pgvector, FastAPI, React | §13 · [diseño](docs/asistente-ia.md), [diagramas](docs/diagramas) e **[implementación](asistente/)** — 341 pruebas |
| **4** · Conceptuales | Siete preguntas técnicas, ancladas en este código | — | §14 · [respuestas](docs/preguntas-tecnicas.md) |
| **5** · SQL *(bonus)* | Facturas vencidas y saldo por cliente | T-SQL / Azure SQL | §12 · [consultas](sql/consultas.sql) |
| **6** · Low-code *(bonus)* | Este mismo job en Power Automate frente a Python | — | §15 · [comparación](docs/low-code-vs-codigo.md) |

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

El umbral de 10 días es configurable (`OVERDUE_ALERT_THRESHOLD_DAYS`). El envío de correo tiene **dos canales intercambiables** (`NOTIFICATION_CHANNEL`): `log` simula la entrega con líneas de log —default, sin red— y `smtp` **envía correo real**. Contra [Mailpit](https://mailpit.axllent.org/) los mensajes llegan a una bandeja local y se pueden abrir uno por uno; contra Gmail, Microsoft 365 o SendGrid llegan al cliente. Los dos casos están ejecutados y comparados en la [§9.1](#91-envío-real-por-smtp).

De dónde salen estas reglas, qué se dio por supuesto y qué quedó fuera del alcance: [`Bloque 1 - Aterrizaje de Requerimiento.docx`](docs/Bloque%201%20-%20Aterrizaje%20de%20Requerimiento.docx).

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
      notifier.py          entrega: log simulado  o  SMTP real
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
| `app/notifier.py` | Entrega de notificaciones | Log **o** SMTP |
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
| `NOTIFICATION_CHANNEL` | `log` | `log` simula el envío; `smtp` **envía correo real** |
| `SMTP_HOST` | `localhost` | Servidor SMTP (Mailpit en local) |
| `SMTP_PORT` | `1025` | Puerto SMTP (Mailpit escucha en 1025) |
| `SMTP_USERNAME` | *(vacío)* | Vacío → no se autentica. Mailpit no lo necesita |
| `SMTP_PASSWORD` | *(vacío)* | En producción viene de Key Vault, nunca de un archivo |
| `SMTP_USE_TLS` | `false` | `true` emite `STARTTLS` antes de enviar |
| `EMAIL_FROM` | `cobranza@empresa.com` | Remitente |
| `EMAIL_FROM_NAME` | `Cobranza Empresa` | Nombre visible del remitente |

Las rutas relativas se resuelven contra la raíz del proyecto, así que la app funciona desde cualquier directorio de trabajo.

Plantilla: `.env.example`. **Los defaults bastan para ejecutar sin `.env` y sin red.** Si existe un `.env` en la raíz se carga al arrancar, así que un secreto puede vivir en un archivo ignorado por Git en lugar de quedarse en el historial del shell. **El entorno real siempre gana**: un `SMTP_HOST=... python main.py` explícito pisa lo que diga el archivo.

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

### 4.1 Envío real de correo con Mailpit

Con `NOTIFICATION_CHANNEL=smtp` el proceso deja de simular: construye un mensaje MIME por notificación y lo entrega por SMTP. **Mailpit** es el servidor de pruebas que hace real el caso sin riesgo: acepta todos los mensajes, no reenvía nada a internet y los muestra en una bandeja web.

**1. Levantar Mailpit** (SMTP en `1025`, bandeja web en `8025`):

```bash
# Docker
docker run --rm -p 1025:1025 -p 8025:8025 axllent/mailpit

# Binario en Windows, sin instalar nada
Invoke-WebRequest https://github.com/axllent/mailpit/releases/latest/download/mailpit-windows-amd64.zip -OutFile mailpit.zip
Expand-Archive mailpit.zip -DestinationPath mailpit -Force
.\mailpit\mailpit.exe
```

**2. Ejecutar el proceso contra Mailpit:**

```powershell
$env:NOTIFICATION_CHANNEL="smtp"; $env:SMTP_HOST="localhost"; $env:SMTP_PORT="1025"
python main.py
```

```bash
NOTIFICATION_CHANNEL=smtp SMTP_HOST=localhost SMTP_PORT=1025 python main.py   # Linux / macOS
```

**3. Abrir la bandeja:** <http://localhost:8025>. Cada recordatorio aparece dirigido al correo del cliente y cada alerta a `OPERATIONS_EMAIL`.

Notas de diseño:

- **La idempotencia también aplica al correo real.** La segunda ejecución del mismo día no vuelve a enviar nada: la bandeja se queda igual y el log reporta `skipped`. Es exactamente la garantía de la [§7](#idempotencia), ahora verificable en una bandeja.
- **Un buzón caído no aborta el lote.** Cualquier fallo SMTP se convierte en `NotificationError`, se cuenta en `errors` y el proceso sigue con la siguiente factura. El estado de idempotencia **no** se marca, así que la próxima ejecución reintenta.
- **Una conexión por mensaje.** El lote son unas cuantas facturas por corrida; una conexión de vida corta no puede quedar obsoleta entre dos envíos lentos.
- **Producción es la misma clase, otro entorno.** `SMTP_HOST`, `SMTP_USERNAME` y `SMTP_USE_TLS` apuntando a Microsoft 365, SendGrid o Azure Communication Services envían al cliente real sin tocar una línea de código.

### 4.2 Entrega real a internet

Mailpit valida el mensaje, no la entrega: intercepta todo y no reenvía. Para que los correos salgan de verdad hace falta un **relay autenticado**, porque la entrega directa al MX del destinatario (puerto 25) está bloqueada en cualquier red corporativa. Pon las credenciales en `.env` — está en `.gitignore`, así no acaban en el historial del shell ni en el repositorio:

```ini
NOTIFICATION_CHANNEL=smtp
SMTP_HOST=smtp.office365.com     # o smtp.gmail.com / smtp.sendgrid.net
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=cuenta@dominio.com
SMTP_PASSWORD=                   # app password o API key, nunca la contraseña normal
EMAIL_FROM=cuenta@dominio.com    # debe coincidir con la cuenta autenticada, o el proveedor rechaza el envío
```

```bash
python main.py
```

Dos cosas que muerden en producción y no en Mailpit:

- **`EMAIL_FROM` tiene que pertenecer a un dominio autorizado.** Un remitente arbitrario se rechaza en el `MAIL FROM` o llega marcado como spam.
- **SPF, DKIM y DMARC son del dominio, no de la aplicación.** Sin ellos la entregabilidad depende de la suerte, por muy correcto que sea el mensaje.

**Verificado contra Gmail** el 2026-08-28: `smtp.gmail.com:587` con STARTTLS y una contraseña de aplicación. Salieron 3 correos, quedaron registrados en *Enviados* de la cuenta y **no hubo ni un rebote**, así que los servidores de destino los aceptaron. La segunda corrida inmediata reportó `skipped=3` y no envió nada: la idempotencia se sostiene igual contra un proveedor real que contra Mailpit.

Gmail exige contraseña de aplicación; la contraseña normal de la cuenta se rechaza con `534 5.7.9 Application-specific password required`. Y `EMAIL_FROM` debe ser la cuenta autenticada: Gmail reescribe o rechaza cualquier otro remitente.

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
run 1 -> fetched=16 invalid=1 overdue=8 reminders=8 alerts=4 skipped=0  errors=0
run 2 -> fetched=16 invalid=1 overdue=8 reminders=0 alerts=0 skipped=12 errors=0   <- idempotente

# SIN volumen
run 1 -> fetched=16 invalid=1 overdue=8 reminders=8 alerts=4 skipped=0 errors=0
run 2 -> fetched=16 invalid=1 overdue=8 reminders=8 alerts=4 skipped=0 errors=0   <- reenvía todo
```

**Enviando correo real desde el contenedor:** `localhost` dentro del contenedor no es el host, así que Mailpit hay que alcanzarlo por su nombre de red.

```bash
docker run --env-file .env -v "$(pwd)/state:/app/state" \
  -e NOTIFICATION_CHANNEL=smtp -e SMTP_HOST=host.docker.internal \
  invoice-reminder
```

En Linux añade `--add-host=host.docker.internal:host-gateway`, o pon ambos contenedores en la misma red de Docker y usa `-e SMTP_HOST=mailpit`.

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

71 pruebas, todas deterministas: `today` se inyecta siempre, no hay llamadas de red ni sockets SMTP y no se duerme de verdad (`sleep` es inyectable).

| Archivo | Cubre |
|---|---|
| `test_invoice_service.py` | Reglas de negocio, bordes 10/11 días, estados, parseo y registros corruptos |
| `test_idempotency.py` | Store, escritura atómica, archivo corrupto, `DRY_RUN`, y **segunda ejecución completa → 0 notificaciones** |
| `test_api_client.py` | Qué se reintenta y qué no, `Retry-After`, crecimiento del backoff, cabecera Bearer, fuente de archivo |
| `test_notifier.py` | Destinatarios y contenido del correo, TLS y credenciales solo si están configuradas, fallos SMTP → `NotificationError`, elección del canal |
| `test_config.py` | Carga del `.env`: precedencia del entorno real, comentarios, comillas, contraseñas con `=`, archivo ausente |

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

Ejecución real, dataset del repositorio, fecha de referencia 2026-08-29.

**Primera ejecución:**

```text
2026-08-29 02:06:36 INFO     Process started source=file run_date=2026-08-29 threshold_days=10 channel=log dry_run=False
2026-08-29 02:06:36 INFO     Invoices fetched count=16
2026-08-29 02:06:36 WARNING  Invoice discarded invoice=INV-9999 reason=invalid_due_date
2026-08-29 02:06:36 INFO     Payment reminder sent to=cliente@empresa.com invoice=INV-1003 customer="Empresa Demo" amount=15000.50 currency=MXN due_date=2026-08-24
2026-08-29 02:06:36 INFO     Payment reminder sent to=finanzas@meridiano.mx invoice=INV-1004 customer="Grupo Meridiano" amount=4780.00 currency=MXN due_date=2026-08-19
2026-08-29 02:06:36 INFO     Payment reminder sent to=tesoreria@textilesbajio.mx invoice=INV-1005 customer="Textiles del Bajio" amount=61250.00 currency=MXN due_date=2026-08-14
2026-08-29 02:06:36 WARNING  Operations alert sent to=o5n3it82yy@lnovic.com invoice=INV-1005 customer="Textiles del Bajio" days_overdue=15 amount=61250.00 currency=MXN
2026-08-29 02:06:36 INFO     Payment reminder sent to=kayelo3614@neowd.com invoice=INV-1006 customer="Servicios Integrales Lopez" amount=3120.30 currency=MXN due_date=2026-08-26
2026-08-29 02:06:36 INFO     Payment reminder sent to=kayelo3614@neowd.com invoice=INV-1007 customer="Logistica Pacifico" amount=98500.00 currency=MXN due_date=2026-08-04
2026-08-29 02:06:36 WARNING  Operations alert sent to=o5n3it82yy@lnovic.com invoice=INV-1007 customer="Logistica Pacifico" days_overdue=25 amount=98500.00 currency=MXN
2026-08-29 02:06:36 INFO     Payment reminder sent to=pagos@aurora.mx invoice=INV-2001 customer="Comercial Aurora" amount=12750.00 currency=MXN due_date=2026-08-25
2026-08-29 02:06:36 INFO     Payment reminder sent to=contabilidad@zenit.mx invoice=INV-2003 customer="Constructora Zenit" amount=45300.00 currency=MXN due_date=2026-08-11
2026-08-29 02:06:36 WARNING  Operations alert sent to=o5n3it82yy@lnovic.com invoice=INV-2003 customer="Constructora Zenit" days_overdue=18 amount=45300.00 currency=MXN
2026-08-29 02:06:36 INFO     Payment reminder sent to=finanzas@meridiano.mx invoice=INV-2005 customer="Grupo Meridiano" amount=28400.00 currency=MXN due_date=2026-07-27
2026-08-29 02:06:36 WARNING  Operations alert sent to=o5n3it82yy@lnovic.com invoice=INV-2005 customer="Grupo Meridiano" days_overdue=33 amount=28400.00 currency=MXN
2026-08-29 02:06:36 INFO     Process finished fetched=16 invalid=1 overdue=8 reminders=8 alerts=4 skipped=0 errors=0
```

**Segunda ejecución, inmediatamente después — la idempotencia en acción:**

```text
2026-08-29 02:06:38 INFO     Process finished fetched=16 invalid=1 overdue=8 reminders=0 alerts=0 skipped=12 errors=0
```

Mismas 8 facturas vencidas, **0 notificaciones enviadas, 12 omitidas**.

### 9.1 Envío real por SMTP

Misma ejecución con `NOTIFICATION_CHANNEL=smtp`, fecha de referencia 2026-08-29, contra los dos servidores. Dos facturas del dataset apuntan a buzones temporales reales, y `OPERATIONS_EMAIL` a un tercero, para poder leer el correo como lo lee el destinatario.

```text
2026-08-29 02:08:18 INFO     Process started source=file run_date=2026-08-29 threshold_days=10 channel=smtp dry_run=False
2026-08-29 02:08:18 INFO     Invoices fetched count=16
2026-08-29 02:08:18 WARNING  Invoice discarded invoice=INV-9999 reason=invalid_due_date
2026-08-29 02:08:19 INFO     Email sent via=smtp to=cliente@empresa.com invoice=INV-1003 type=reminder subject='Recordatorio de pago - Factura INV-1003'
2026-08-29 02:08:20 INFO     Email sent via=smtp to=finanzas@meridiano.mx invoice=INV-1004 type=reminder subject='Recordatorio de pago - Factura INV-1004'
2026-08-29 02:08:21 INFO     Email sent via=smtp to=tesoreria@textilesbajio.mx invoice=INV-1005 type=reminder subject='Recordatorio de pago - Factura INV-1005'
2026-08-29 02:08:22 INFO     Email sent via=smtp to=o5n3it82yy@lnovic.com invoice=INV-1005 type=operations_alert subject='[ALERTA] Factura INV-1005 con 15 dias de atraso'
2026-08-29 02:08:23 INFO     Email sent via=smtp to=kayelo3614@neowd.com invoice=INV-1006 type=reminder subject='Recordatorio de pago - Factura INV-1006'
2026-08-29 02:08:24 INFO     Email sent via=smtp to=kayelo3614@neowd.com invoice=INV-1007 type=reminder subject='Recordatorio de pago - Factura INV-1007'
2026-08-29 02:08:25 INFO     Email sent via=smtp to=o5n3it82yy@lnovic.com invoice=INV-1007 type=operations_alert subject='[ALERTA] Factura INV-1007 con 25 dias de atraso'
2026-08-29 02:08:26 INFO     Email sent via=smtp to=pagos@aurora.mx invoice=INV-2001 type=reminder subject='Recordatorio de pago - Factura INV-2001'
2026-08-29 02:08:27 INFO     Email sent via=smtp to=contabilidad@zenit.mx invoice=INV-2003 type=reminder subject='Recordatorio de pago - Factura INV-2003'
2026-08-29 02:08:28 INFO     Email sent via=smtp to=o5n3it82yy@lnovic.com invoice=INV-2003 type=operations_alert subject='[ALERTA] Factura INV-2003 con 18 dias de atraso'
2026-08-29 02:08:29 INFO     Email sent via=smtp to=finanzas@meridiano.mx invoice=INV-2005 type=reminder subject='Recordatorio de pago - Factura INV-2005'
2026-08-29 02:08:29 INFO     Email sent via=smtp to=o5n3it82yy@lnovic.com invoice=INV-2005 type=operations_alert subject='[ALERTA] Factura INV-2005 con 33 dias de atraso'
2026-08-29 02:08:29 INFO     Process finished fetched=16 invalid=1 overdue=8 reminders=8 alerts=4 skipped=0 errors=0
```

12 mensajes: 8 recordatorios a los clientes y 4 alertas a Operaciones. `INV-1004`, con exactamente 10 días de atraso, recibe recordatorio y **ninguna** alerta — el borde del umbral, verificable en la bandeja.

**Idempotencia sobre correo real.** Segunda ejecución inmediata: `skipped=12`, cero envíos, la bandeja intacta en 12 mensajes. Ni un duplicado.

**Fallo de entrega.** Apuntando a un puerto SMTP muerto, cada envío falla, se cuenta y el lote continúa:

```text
app.notifier.NotificationError: smtp_delivery_failed invoice=INV-1003 type=reminder: [WinError 10061] ...
Process finished fetched=16 invalid=1 overdue=8 reminders=0 alerts=0 skipped=0 errors=12
exit code = 1
```

El estado **no** se escribió, así que la siguiente ejecución reintenta. Es la contraparte del *at-least-once* de la [§7](#idempotencia).

#### Los dos canales prueban cosas distintas

La misma corrida por Mailpit y por Gmail no da el mismo resultado, y ahí está el motivo de que el canal sea configurable:

| | Mailpit | Gmail |
|---|---|---|
| Los mensajes del lote | Enviados | Enviados |
| `cliente@empresa.com` | Visible en la bandeja | **Rebotó** — el buzón no existe |
| `tesoreria@textilesbajio.mx` | Visible en la bandeja | **Rebotó** — el buzón no existe |
| Duración | ~7 s | ~24 s (TLS y login por mensaje) |

**Mailpit valida el mensaje**: que se construye bien, que va a quien debe y que el HTML se ve. **Gmail valida la entrega**: que un servidor real lo acepta y que el buzón destino existe. Ninguno sustituye al otro.

Al alternar entre ambos no basta con cambiar `SMTP_HOST`: Mailpit no ofrece STARTTLS, así que hay que bajar también `SMTP_USE_TLS` y vaciar usuario y contraseña, o el login falla con `STARTTLS extension not supported by server`.

#### El contenido

Los correos son `multipart/alternative`: parte HTML con el diseño y parte de texto plano **completa**, no un resto degradado, para quien no renderiza HTML o lo tiene desactivado. El texto va primero en el árbol MIME, que es el orden que hace que un cliente sin HTML encuentre la versión legible.

Recordatorio al cliente (`INV-1007`, 25 días de atraso), parte de texto:

```text
Asunto: Recordatorio de pago - Factura INV-1007
De:     Cobranza Empresa <cobranza@empresa.com>
Para:   kayelo3614@neowd.com

Apreciable Logistica Pacifico:

Por este medio le informamos, de manera atenta, que a la fecha se encuentra
pendiente de pago la factura INV-1007, con vencimiento el 3 de agosto de 2026.

    Factura                INV-1007
    Importe                $98,500.00 MXN
    Fecha de vencimiento   3 de agosto de 2026
    Días transcurridos     25 días

Le agradeceremos cubrir el importe correspondiente a la brevedad. En caso de
que el pago ya se hubiera realizado, le pedimos hacer caso omiso del presente
aviso o bien compartirnos el comprobante para actualizar el estado de su cuenta.

Quedamos a sus órdenes para cualquier aclaración.

Atentamente,
Departamento de Cobranza
Cobranza Empresa
```

Alerta a Operaciones del mismo caso, que además lleva el contacto del cliente:

```text
Asunto: [ALERTA] Factura INV-1007 con 25 dias de atraso
Para:   o5n3it82yy@lnovic.com

ALERTA DE CARTERA VENCIDA

La factura INV-1007 rebasó el umbral de atraso definido para el
escalamiento a Operaciones y requiere seguimiento.

    Factura                INV-1007
    Cliente                Logistica Pacifico
    Contacto               kayelo3614@neowd.com
    Importe                $98,500.00 MXN
    Fecha de vencimiento   3 de agosto de 2026
    Días de atraso         25 días

El recordatorio de pago correspondiente ya fue enviado al cliente.
```

La parte HTML está escrita para clientes de correo, no para navegadores: tablas en lugar de flexbox, estilos en línea porque Outlook descarta los bloques `<style>` y Gmail elimina el `<head>`, colores declarados explícitamente para que un cliente en modo oscuro no invierta medio mensaje, y **cero recursos externos** — una imagen remota se bloquea por defecto y de paso filtra un acuse de lectura. Los nombres de cliente vienen de la API, así que van escapados: una prueba inyecta `<script>` en el nombre y verifica que sale neutralizado.

### El dataset

16 facturas que cubren todas las ramas de decisión:

| Factura | Cliente | Estado | Atraso | Resultado esperado |
|---|---|---|---|---|
| INV-1001 | Aurora | `paid` | 20 días | Ninguna acción |
| INV-1002 | Del Norte | `pending` | −7 (vigente) | Ninguna acción |
| INV-1003 | Empresa Demo | `pending` | 5 días | Recordatorio |
| INV-1004 | Meridiano | `pending` | **10 días (borde)** | Recordatorio, **sin** alerta |
| INV-1005 | Textiles | `pending` | 15 días | Recordatorio + alerta |
| INV-1006 | Logistica | `pending` | 3 días | Recordatorio → **buzón real** |
| INV-1007 | Logistica | `pending` | 25 días | Recordatorio + alerta → **buzones reales** |
| INV-1008 | Zenit | `cancelled` | 40 días | Ninguna acción |
| INV-2001 | Aurora | `pending` | 4 días | Recordatorio |
| INV-2002 | Aurora | `pending` | −12 (vigente) | Ninguna acción |
| INV-2003 | Zenit | `pending` | 18 días | Recordatorio + alerta |
| INV-2004 | Zenit | `paid` | 30 días | Ninguna acción |
| INV-2005 | Meridiano | `pending` | 33 días | Recordatorio + alerta |
| INV-2006 | Meridiano | `paid` | 45 días | Ninguna acción |
| INV-2007 | Logistica | `paid` | 12 días | Ninguna acción |
| INV-9999 | — | `pending` | fecha inválida | Descartada con `WARNING` |

Resultado: `fetched=16 invalid=1 overdue=8 reminders=8 alerts=4`.

Las cuatro cuentas del asistente ([bloque 3](asistente/)) tienen aquí facturas propias, con casos mezclados —pagada, vigente, vencida y por encima del umbral— para que entrar como cualquiera de ellas muestre algo real.

`INV-1006` e `INV-1007` apuntan a buzones temporales reales, y `OPERATIONS_EMAIL` en `.env.example` a un tercero: así el envío real ([§9.1](#91-envío-real-por-smtp)) se puede leer como lo lee el destinatario. El dataset se regenera con `scripts/refresh_sample_data.py`, que es donde viven esas direcciones — editarlas ahí es lo que hace que sobrevivan a un refresco.

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

## 11. Aterrizaje del requerimiento

El punto de partida del ejercicio, en [`docs/Bloque 1 - Aterrizaje de Requerimiento.docx`](docs/Bloque%201%20-%20Aterrizaje%20de%20Requerimiento.docx).

El escenario original era deliberadamente ambiguo: *«que el sistema avise cuando algo esté por vencerse y que se resuelva solo»*. El documento no lo interpreta y sigue adelante, sino que separa lo que el escenario **establece** de lo que **no**, y convierte cada hueco en una pregunta con dueño.

Lo que contiene:

- **Preguntas de clarificación** en cinco grupos —qué se vence, qué significa «resolverse solo», usuarios y canales, datos y sistemas, éxito y negocio— distinguiendo las **bloqueantes** de las que no lo son.
- **Registro de hipótesis** con responsable de validación e impacto si resulta falsa.
- **Alcance dentro y fuera del MVP**, cada exclusión con su motivo y si vuelve en una fase posterior.
- **Niveles de autonomía**, que es la forma de aterrizar «resolverse solo» sin prometer magia: del aviso al humano hasta la acción automática reversible.
- **Estimación por t-shirt sizing** por componente, con los factores que pueden mover la talla.
- **Riesgos y dependencias externas**, con mitigación, disparador y responsable.
- **Criterios de aceptación** en formato Dado/Cuando/Entonces, **incluyendo escenarios negativos**: qué debe pasar cuando la fuente no responde, cuando un caso ya fue avisado, cuando hay una prórroga vigente.
- **Tres métricas de éxito** con línea base y momento de medición: fuga de vencimientos, esfuerzo percibido del usuario y desviación de alcance y tiempo.

---

## 12. Consultas SQL

En [`sql/consultas.sql`](sql/consultas.sql), en T-SQL (Azure SQL / SQL Server), validadas ejecutándolas contra SQL Server 2019:

1. **Facturas con más de 10 días de atraso.**
2. **Saldo vencido y promedio de días de atraso por cliente**, con `JOIN`, `GROUP BY` y `HAVING` sobre un umbral parametrizado.

El archivo incluye un juego de datos de prueba con los casos límite, para que los resultados se puedan reproducir. Tres decisiones que vale la pena señalar:

- **Sólo cuentan las facturas `pending`.** Una factura pagada hace meses sigue teniendo la fecha de vencimiento en el pasado; sin ese filtro aparecería como vencida.
- **El filtro no envuelve la columna en una función**: `FechaVencimiento < DATEADD(...)` en lugar de `DATEDIFF(...) > 10`. Mismo resultado, pero la segunda forma anula el uso del índice.
- **El promedio necesita `CAST`.** `DATEDIFF` devuelve entero y `AVG` sobre enteros hace división entera: con los datos de prueba, sin el `CAST` el promedio sale `10` en lugar de `10.75`.

Igual que en el código Python, la fecha de hoy es un parámetro y no `GETDATE()` incrustado en el `WHERE`, así el resultado de cualquier día es reproducible.

---

## 13. Asistente interno de IA

Diseño de arquitectura y flujo en [`docs/asistente-ia.md`](docs/asistente-ia.md), con los diagramas como SVG versionados en [`docs/diagramas/`](docs/diagramas). **Y una implementación que corre**, en [`asistente/`](asistente): 341 pruebas, Postgres con pgvector, y el proveedor de modelo intercambiable entre Azure OpenAI, Vertex AI y Gemini.

**Decisión:** Azure OpenAI con RAG, orquestado desde un backend en Python (FastAPI) con una SPA en React — no Copilot Studio. La razón va más allá del stack: las dos intenciones necesitan *formas de datos distintas*. Las pólizas son documentos y se resuelven con recuperación semántica; el estado de una factura es un dato transaccional, vivo y con permisos por cliente, que se resuelve llamando al sistema de registro. Indexar facturas en un almacén vectorial dejaría el dato desactualizado y convertiría el índice en un canal de fuga entre clientes.

Los tres puntos que sostienen el diseño:

- **El recorte por permisos vive en la recuperación**, no en el prompt. El predicado de visibilidad va dentro de la consulta de ranking: un fragmento no autorizado no es un candidato descartado, no llega a puntuar.
- **Las cifras no las escribe el modelo.** Importes, fechas, estatus —y también la fecha de hoy y la lista de capacidades— se insertan por código. El modelo elige la ruta y redacta alrededor del dato, nunca lo produce.
- **Nada se afirma sin evidencia.** Si la respuesta cita un fragmento que no se recuperó —la firma exacta de una respuesta fabricada— se descarta.


Para validar el asistente puedes ingresar con los siguientes datos. La contraseña es `asistente2026` en las cuatro cuentas:

| Cuenta | Cliente |
|---|---|
| `pagos@aurora.mx` | Comercial Aurora |
| `finanzas@meridiano.mx` | Grupo Meridiano |
| `kayelo3614@neowd.com` | Logistica Pacifico |
| `contabilidad@zenit.mx` | Constructora Zenit |

Entrar con dos de ellas en ventanas distintas es la forma más rápida de ver el aislamiento: los documentos, las facturas y los casos escalados de una no existen para la otra.

Si también quieres la URL en asistente/README.md, va antes de ### Base de datos (línea 97):

### Probarlo en vivo

<https://mhns6gbq-5173.usw3.devtunnels.ms/>
---

## 14. Preguntas técnicas conceptuales

Respuestas en [`docs/preguntas-tecnicas.md`](docs/preguntas-tecnicas.md), ancladas en lo que este repositorio implementa: webhook frente a API y polling, idempotencia, ROI de una automatización, consumo de una API REST desde Power Automate y desde Python, gestión de secretos, alucinaciones y fuga de datos, y estrategia de ramas y CI/CD.

También en Word: [`docs/preguntas-tecnicas.docx`](docs/preguntas-tecnicas.docx). **El markdown es la fuente de verdad**; el `.docx` se regenera, no se edita a mano:

```bash
pip install -r requirements-dev.txt
python scripts/generar_docx.py
```

---

## 15. Power Automate frente a Python

Comparación de este mismo job hecho en *low-code* contra código, en [`docs/low-code-vs-codigo.md`](docs/low-code-vs-codigo.md) y en Word en [`docs/low-code-vs-codigo.docx`](docs/low-code-vs-codigo.docx).

**Veredicto:** Python fue la decisión correcta, pero no por goleada, y lo que la decide es **el estado por entidad** — no la llamada a la API ni las reglas de negocio, que Power Automate maneja bien. Fue la idempotencia con garantía *at-least-once* y las reglas de fecha que necesitan pruebas.

Los tres lugares donde el *low-code* se rompe para este caso: la aritmética de fechas queda irrevisable dentro de una expresión del diseñador, la idempotencia hay que construirla sobre una lista externa sin escritura atómica, y no existe framework de pruebas — así que no hay compuerta antes de producción.

Donde Power Automate gana de verdad: humanos en el ciclo (aprobaciones, tarjetas en Teams), pegamento entre servicios de Microsoft 365, y que no hay imagen que parchear. La pregunta que más rápido decide no es técnica: **¿quién es el dueño del cambio?**

---

## 16. Demostración en video

**[Ver el video](https://drive.google.com/file/d/1H_lIz7a0iXisAuRdzHDJFYBie3V7Glyq/view?usp=sharing)** (Google Drive)

Recorrido de la solución en ejecución:

1. **Estructura** — separación por responsabilidad; reglas de negocio como funciones puras.
2. **Primera corrida** — nueve facturas leídas, una descartada por fecha inválida sin abortar el lote.
3. **Envío de correo real** — los recordatorios y las alertas llegando a una bandeja, con el contenido que ve el destinatario ([§9.1](#91-envío-real-por-smtp)).
4. **Idempotencia** — el mismo comando el mismo día no envía ni un recordatorio repetido.
5. **Manejo de errores** — API inalcanzable: reintentos con backoff creciente y código de salida distinto de cero.
6. **Pruebas** — la suite completa, determinista.
7. **Docker** — el contenedor corriendo como usuario no privilegiado.
8. **Configuración por entorno** — bajar el umbral de días sin reconstruir la imagen.

El video no se versiona en el repositorio a propósito: un binario de decenas de megabytes queda en el historial de git para siempre y lo descarga cualquiera que clone, aunque no lo necesite.

---

## 17. Limitaciones y siguientes pasos

Lo que quedó fuera a propósito por el alcance de la prueba:

- **El archivo de estado crece sin límite.** No hay purga de claves antiguas. En producción se resuelve con TTL (Cosmos DB, Redis) o con un job de limpieza.
- **El envío SMTP no reintenta dentro de la corrida.** Un fallo se cuenta y queda para la ejecución siguiente, que reintenta porque el estado no se marcó. Con un proveedor real haría falta una dead-letter queue y backoff por mensaje, como el que ya tiene el cliente de API.
- **Los rebotes no se procesan.** El SMTP del proveedor acepta el mensaje y el job lo da por enviado; si el buzón destino no existe, el rebote llega después, por correo, fuera del alcance del proceso. Se comprobó enviando por Gmail a dos direcciones inexistentes del dataset: `errors=0` y las notificaciones marcadas como procesadas, con los rebotes en la bandeja del remitente. Un sistema de cobranza real consumiría esos avisos para marcar la dirección como inválida y dejar de reintentar.
- **Sin adjuntos.** Los correos no llevan el PDF de la factura; es un añadido directo sobre `SmtpNotifier`.
- **Sin concurrencia.** Dos instancias simultáneas pueden duplicar notificaciones, porque el archivo local no da exclusión mutua. Un store real con escritura condicional (ETag en Table Storage, `SETNX` en Redis) lo resuelve.
- **`Retry-After` solo en formato de segundos**; la variante con fecha HTTP cae al backoff normal.
- **Sin paginación** en el cliente de API, conforme al contrato definido.
- **Fechas naive en hora local.** Con clientes en varias zonas horarias habría que fijar la zona de negocio explícitamente, porque "vencida" es una afirmación relativa a un huso.
