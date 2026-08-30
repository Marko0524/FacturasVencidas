# Un solo contenedor: la API sirve también la interfaz ya compilada.
#
# Podrían ser dos servicios de Cloud Run, pero serían dos despliegues que
# mantener en sintonía, dos URL y un CORS de verdad —el de ahora existe solo
# porque Vite vive en otro puerto en desarrollo—. Con un origen único, nada de
# eso hace falta, y no se puede desplegar una mitad y olvidar la otra.

# --- 1. la interfaz -----------------------------------------------------------
FROM node:22-slim AS interfaz

WORKDIR /interfaz

# Primero el manifiesto y después el código: mientras no cambien las
# dependencias, Docker reutiliza la capa del `npm ci`, que es la lenta.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- 2. el servicio -----------------------------------------------------------
FROM python:3.11-slim AS servicio

# PYTHONUNBUFFERED para que los logs salgan cuando ocurren y no cuando se llena
# el búfer: sin esto, en Cloud Run los registros de un arranque fallido llegan
# después de que el contenedor haya muerto, o no llegan.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY data ./data
COPY --from=interfaz /interfaz/dist ./static

# Corre sin privilegios. Cloud Run ya aísla el contenedor, pero si un día esto
# se ejecuta en otro sitio, no hay motivo para que sea root.
RUN useradd --create-home --uid 10001 asistente && chown -R asistente /app
USER asistente

ENV STATIC_PATH=/app/static \
    CORPUS_PATH=/app/data/polizas \
    INVOICES_PATH=/app/data/facturas.json \
    RETRIEVAL_BACKEND=postgres \
    AUTH_MODE=local \
    LLM_PROVIDER=vertex \
    PORT=8080

EXPOSE 8080

# Cloud Run inyecta PORT y puede cambiarlo, así que se lee en tiempo de
# ejecución en vez de fijarlo aquí. Va por `sh -c` porque en forma exec no hay
# shell que expanda la variable, y uvicorn recibiría "$PORT" como texto.
#
# Un solo worker a propósito: Cloud Run escala con instancias, no con procesos
# dentro de una. Varios workers competirían por la memoria de la misma
# instancia y multiplicarían las conexiones a Cloud SQL sin servir más.
CMD ["sh", "-c", "exec uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
