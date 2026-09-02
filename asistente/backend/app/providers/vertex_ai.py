"""Google Vertex AI provider, authenticated with a service account.

The same models as the Gemini provider, reached the way an organisation is
usually required to reach them. It exists because a real corporate policy asked
for it: the project that hosts this deliverable forbids API keys outright —

    "La política de seguridad de tu organización no permite las claves de API.
     Usa las credenciales predeterminadas de la aplicación (ADC) en su lugar."

— which is the same reasoning the design document gives for Key Vault and
Managed Identity over secrets in files. A long-lived key that can be copied out
of a laptop is exactly what that policy is trying to prevent.

Three things differ from the AI Studio API, and all three are why this is a
separate class rather than a flag:

* **Auth.** A short-lived OAuth2 bearer token, minted from the service account
  and refreshed on expiry, instead of a static key in a header.
* **Address.** The model lives under a project and a location, not at a bare
  model id.
* **Embeddings.** A different endpoint (``:predict``) with a different payload
  and a different response shape.
"""

from __future__ import annotations

import logging

import requests

from app.config import VertexSettings
from app.providers.base import ProviderError, presupuesto_de_razonamiento
from app.providers.http import post_json

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class VertexAIProvider:
    """Chat and embeddings against Vertex AI with a service account."""

    name = "vertex"

    def __init__(self, settings: VertexSettings, timeout: float = 30.0) -> None:
        try:
            import google.auth
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderError(
                "Vertex AI needs google-auth: pip install -r requirements.txt"
            ) from exc

        proyecto_credencial = ""
        # Se distingue "no se configuró ninguna ruta" de "se configuró y no se
        # puede leer". Lo primero es el caso de Cloud Run y va a la identidad
        # adjunta; lo segundo es una errata, y caer ahí en la identidad del
        # entorno sería silenciar un error para acabar hablando con Vertex como
        # otra cuenta. Falla ruidoso, que es lo que hacía antes.
        if settings.credentials_path is not None:
            if not settings.credentials_path.is_file():
                raise ProviderError(
                    f"no service account file at {settings.credentials_path}"
                )
            # En una máquina de desarrollo se apunta a un JSON descargado.
            try:
                self._credentials = service_account.Credentials.from_service_account_file(
                    str(settings.credentials_path), scopes=SCOPES
                )
            except (OSError, ValueError) as exc:
                raise ProviderError(
                    f"could not read the service account at {settings.credentials_path}: {exc}"
                ) from exc
            proyecto_credencial = self._credentials.project_id or ""
        else:
            # En Cloud Run —o en cualquier sitio de Google— la identidad la da
            # la cuenta de servicio adjunta y no hay archivo que leer.
            #
            # Es la forma correcta de desplegar esto, no un atajo: meter un JSON
            # de credenciales en la imagen lo deja en una capa para siempre, lo
            # reparte a cualquiera que pueda hacer `docker pull`, y hay que
            # rotarlo a mano. Con la cuenta adjunta no existe llave que filtrar.
            try:
                self._credentials, proyecto_credencial = google.auth.default(scopes=SCOPES)
            except Exception as exc:  # noqa: BLE001 - google.auth lanza lo suyo
                raise ProviderError(
                    "Vertex AI no está configurado: apunta GOOGLE_APPLICATION_CREDENTIALS "
                    "al JSON de la cuenta de servicio, o ejecuta donde haya credenciales "
                    f"por defecto (Cloud Run, GCE, `gcloud auth application-default login`): {exc}"
                ) from exc

        # La credencial trae su propio proyecto. Fiarse de él antes que de un
        # valor configurado aparte evita toda una clase de 403 confusos, en los
        # que la credencial es válida pero apunta a otro sitio que la petición.
        self._project = settings.project or proyecto_credencial
        if not self._project:
            raise ProviderError("no project: set VERTEX_PROJECT or use a JSON that names one")

        self._location = settings.location
        self._settings = settings
        self._timeout = timeout
        self._session = requests.Session()

    # --- provider interface ---------------------------------------------------

    def complete(self, *, system: str, user: str, max_tokens: int = 800) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "thinkingConfig": {"thinkingBudget": presupuesto_de_razonamiento(max_tokens)},
            },
        }
        data = self._post(self._settings.chat_model, "generateContent", payload)
        return self._first_text(data)

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"instances": [{"content": text} for text in texts]}
        data = self._post(self._settings.embedding_model, "predict", payload)
        try:
            return [item["embeddings"]["values"] for item in data["predictions"]]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected Vertex AI embeddings payload: {data}") from exc

    # --- plumbing -------------------------------------------------------------

    @staticmethod
    def _first_text(data: dict) -> str:
        """Pull the answer out, and turn a silent block into a loud failure.

        A response filtered by safety settings, or one where a reasoning model
        spent the whole budget thinking, comes back with no ``parts`` at all.
        Returning "" there would look like a model with nothing to say; raising
        sends it down the escalation path, where it belongs.
        """
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "sin candidatos")
            raise ProviderError(f"Vertex AI returned no answer (reason={reason})")

        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text:
            raise ProviderError(
                f"Vertex AI returned an empty answer "
                f"(finishReason={candidate.get('finishReason')})"
            )
        # Una respuesta a medias es peor que ninguna: el JSON cortado llega
        # arriba como "el modelo no devuelve JSON válido", que manda a leer el
        # prompt cuando lo que faltaron fueron tokens. Se dice aquí, donde se
        # sabe por qué.
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise ProviderError(
                "Vertex AI truncated the answer (finishReason=MAX_TOKENS): "
                "raise max_tokens or lower the thinking budget"
            )
        return text

    def _host(self) -> str:
        """``global`` is not a region, so it does not get a regional host."""
        if self._location == "global":
            return "aiplatform.googleapis.com"
        return f"{self._location}-aiplatform.googleapis.com"

    def _token(self) -> str:
        """A fresh access token, refreshed only when the current one is stale.

        Unlike an API key this expires, which is the entire point: a token
        copied out of a process stops working within the hour.
        """
        if not self._credentials.valid:
            import google.auth.transport.requests as transport

            try:
                self._credentials.refresh(transport.Request())
            except Exception as exc:  # noqa: BLE001 - google-auth raises broadly
                raise ProviderError(f"could not mint a Vertex AI token: {exc}") from exc
        return self._credentials.token

    def _post(self, model: str, operation: str, payload: dict) -> dict:
        url = (
            f"https://{self._host()}/v1/projects/{self._project}"
            f"/locations/{self._location}/publishers/google/models/{model}:{operation}"
        )
        return post_json(
            self._session,
            url,
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=self._timeout,
            provider="Vertex AI",
        )
