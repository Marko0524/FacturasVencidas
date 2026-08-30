#!/usr/bin/env bash
#
# Despliegue en Cloud Run.
#
#   ./desplegar.sh mi-proyecto
#
# Es idempotente: crea lo que falte y deja igual lo que ya exista, así que se
# puede volver a ejecutar para publicar una versión nueva.
#
# Lo que NO hace, a propósito: no crea la cuenta de facturación ni acepta
# términos por ti, y no borra nada. Cloud SQL cuesta dinero mientras exista;
# apagarlo es una decisión tuya y va al final de este archivo.

set -euo pipefail

PROYECTO="${1:?uso: ./desplegar.sh <id-del-proyecto> [region]}"

# Dónde corren el servicio y la base de datos. NO es dónde vive Vertex: el
# endpoint de Vertex se configura aparte y por omisión es "global", que es el
# que está probado. Mezclar las dos cosas cambiaría el endpoint del modelo al
# desplegar en otra región, con un 404 de modelo no disponible como resultado.
REGION="${2:-us-central1}"

SERVICIO="asistente-polizas"
INSTANCIA="asistente-db"
BASE="asistente"
USUARIO="asistente"
CUENTA="asistente-run"
SECRETO_SESION="asistente-session-secret"
SECRETO_BD="asistente-db-password"

gcloud config set project "$PROYECTO" >/dev/null
echo "==> Proyecto $PROYECTO, región $REGION"

echo "==> Habilitando APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# --- secretos -----------------------------------------------------------------
# Se generan aquí y no se imprimen. El de sesión firma los tokens: si cambia,
# todo el mundo tiene que volver a entrar, así que se crea una sola vez.
crear_secreto_si_falta() {
  local nombre="$1" valor="$2"
  if gcloud secrets describe "$nombre" >/dev/null 2>&1; then
    echo "==> Secreto $nombre ya existe, se conserva"
  else
    echo "==> Creando secreto $nombre"
    printf '%s' "$valor" | gcloud secrets create "$nombre" --data-file=- --replication-policy=automatic
  fi
}

crear_secreto_si_falta "$SECRETO_SESION" "$(openssl rand -base64 32)"
crear_secreto_si_falta "$SECRETO_BD" "$(openssl rand -base64 24 | tr -d '/+=')"
CLAVE_BD="$(gcloud secrets versions access latest --secret="$SECRETO_BD")"

# --- base de datos ------------------------------------------------------------
if gcloud sql instances describe "$INSTANCIA" >/dev/null 2>&1; then
  echo "==> Cloud SQL $INSTANCIA ya existe"
else
  # Postgres 17 por pgvector, que en Cloud SQL está disponible desde la 15.
  # `db-g1-small` es lo más pequeño que aguanta la extensión con holgura; para
  # producción de verdad, sube de ahí.
  echo "==> Creando Cloud SQL $INSTANCIA (tarda unos minutos)"
  gcloud sql instances create "$INSTANCIA" \
    --database-version=POSTGRES_17 \
    --tier=db-g1-small \
    --region="$REGION" \
    --storage-auto-increase \
    --database-flags=cloudsql.enable_pgvector=on
fi

gcloud sql databases create "$BASE" --instance="$INSTANCIA" 2>/dev/null || echo "==> Base $BASE ya existe"
gcloud sql users create "$USUARIO" --instance="$INSTANCIA" --password="$CLAVE_BD" 2>/dev/null \
  || gcloud sql users set-password "$USUARIO" --instance="$INSTANCIA" --password="$CLAVE_BD"

CONEXION="$(gcloud sql instances describe "$INSTANCIA" --format='value(connectionName)')"

# --- identidad ----------------------------------------------------------------
# Una cuenta de servicio propia con lo justo. Nada de descargar su JSON: Cloud
# Run se la adjunta al contenedor y las credenciales por defecto la encuentran
# solas. Una llave descargada es una llave que se puede filtrar y hay que rotar.
if ! gcloud iam service-accounts describe "$CUENTA@$PROYECTO.iam.gserviceaccount.com" >/dev/null 2>&1; then
  echo "==> Creando cuenta de servicio $CUENTA"
  gcloud iam service-accounts create "$CUENTA" --display-name="Asistente de pólizas (Cloud Run)"
fi
CORREO_CUENTA="$CUENTA@$PROYECTO.iam.gserviceaccount.com"

for rol in roles/aiplatform.user roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROYECTO" \
    --member="serviceAccount:$CORREO_CUENTA" --role="$rol" --condition=None >/dev/null
done
echo "==> Permisos concedidos: Vertex, Cloud SQL y lectura de secretos"

# --- despliegue ---------------------------------------------------------------
# `--source .` deja que Cloud Build construya el Dockerfile: no hace falta tener
# Docker en la máquina desde la que se despliega.
#
# Sin GOOGLE_APPLICATION_CREDENTIALS: su ausencia es lo que hace que el proveedor
# use la identidad adjunta.
echo "==> Desplegando $SERVICIO"
gcloud run deploy "$SERVICIO" \
  --source . \
  --region="$REGION" \
  --service-account="$CORREO_CUENTA" \
  --add-cloudsql-instances="$CONEXION" \
  --set-secrets="SESSION_SECRET=$SECRETO_SESION:latest" \
  --set-env-vars="^@^DATABASE_URL=postgresql://$USUARIO:$CLAVE_BD@/$BASE?host=/cloudsql/$CONEXION@VERTEX_PROJECT=$PROYECTO@LLM_REQUEST_TIMEOUT=90" \
  --cpu=1 \
  --memory=1Gi \
  --timeout=300 \
  --concurrency=20 \
  --min-instances=0 \
  --max-instances=5 \
  --no-allow-unauthenticated

# El servicio queda privado: sin credenciales, la URL responde 403 a cualquiera.
# Quien despliega se concede permiso para invocarlo.
YO="$(gcloud config get-value account 2>/dev/null)"
gcloud run services add-iam-policy-binding "$SERVICIO" \
  --region="$REGION" --member="user:$YO" --role=roles/run.invoker >/dev/null
echo "==> Servicio privado; $YO puede invocarlo"

URL="$(gcloud run services describe "$SERVICIO" --region="$REGION" --format='value(status.url)')"

cat <<FIN

==> Desplegado en $URL

Esa URL da 403 si la abres a pelo, y es lo que se busca: un navegador no manda
el token de identidad que exige Cloud Run. Para usarla, levanta el proxy local,
que se autentica con tu cuenta y expone el servicio en tu máquina:

    gcloud run services proxy $SERVICIO --region=$REGION --port=8080

y abre http://localhost:8080

Escala a cero: la primera consulta tras un rato inactivo tarda unos segundos más
mientras arranca el contenedor. Cloud SQL sí sigue encendida y se paga.

Para dejar de pagar del todo:
    gcloud run services delete $SERVICIO --region=$REGION
    gcloud sql instances delete $INSTANCIA     # esto BORRA los documentos

FIN
