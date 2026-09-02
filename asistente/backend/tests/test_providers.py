"""Provider HTTP behaviour: what is retried, what is not, and what escalates.

No socket is opened: ``requests.Session`` is replaced by a queue of outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from app.config import AzureSettings, GeminiSettings
from app.providers.azure_openai import AzureOpenAIProvider
from app.providers.base import ProviderError
from app.providers.gemini import GeminiProvider


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or "{}"
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeSession:
    """Returns queued responses, or raises queued exceptions, in order."""

    def __init__(self, outcomes: list):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json, "timeout": timeout})
        if not self._outcomes:
            raise AssertionError("the provider made more requests than the test queued")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


CHAT_OK = FakeResponse(200, {"choices": [{"message": {"content": "hola"}}]})
GEMINI_OK = FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": "hola"}]}}]})


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """The backoff is real; waiting for it in a test is not."""
    monkeypatch.setattr("app.providers.http.time.sleep", lambda _seconds: None)


def azure(session) -> AzureOpenAIProvider:
    provider = AzureOpenAIProvider(
        AzureSettings("https://x.openai.azure.com", "k", "chat", "emb", "2024-10-21")
    )
    provider._session = session  # noqa: SLF001 - test seam
    return provider


def gemini(session) -> GeminiProvider:
    provider = GeminiProvider(GeminiSettings("k", "gemini-3.6-flash", "gemini-embedding-001"))
    provider._session = session  # noqa: SLF001 - test seam
    return provider


# --- retry policy ------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_failures_are_retried_then_succeed(status: int):
    session = FakeSession([FakeResponse(status), CHAT_OK])

    assert azure(session).complete(system="s", user="u") == "hola"
    assert len(session.calls) == 2


def test_a_timeout_is_retried():
    session = FakeSession([requests.Timeout("too slow"), CHAT_OK])

    assert azure(session).complete(system="s", user="u") == "hola"


def test_a_dropped_connection_is_retried():
    """El fallo real que se vio en producción, y que este código no reintentaba.

    La sesión mantiene la conexión abierta entre peticiones; el servidor la
    cierra por inactividad; la siguiente petición se encuentra el extremo
    cerrado antes de enviar nada. Reintentar abre una conexión nueva y funciona
    a la primera. Sin esto, un parpadeo de red le costaba al cliente un
    escalamiento con folio.
    """
    session = FakeSession([
        requests.ConnectionError("('Connection aborted.', RemoteDisconnected(...))"),
        CHAT_OK,
    ])

    assert azure(session).complete(system="s", user="u") == "hola"
    assert len(session.calls) == 2


def test_a_connection_that_never_comes_back_still_gives_up():
    """Reintentar no es insistir para siempre."""
    session = FakeSession([requests.ConnectionError("caída") for _ in range(4)])

    with pytest.raises(ProviderError, match="unreachable"):
        azure(session).complete(system="s", user="u")

    assert len(session.calls) == 4


def test_retries_are_bounded_and_then_escalate():
    session = FakeSession([FakeResponse(503) for _ in range(4)])

    with pytest.raises(ProviderError, match="kept failing"):
        azure(session).complete(system="s", user="u")

    assert len(session.calls) == 4  # 1 intento + 3 reintentos


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status: int):
    """A bad key or a deployment that does not exist answers the same forever."""
    session = FakeSession([FakeResponse(status, text='{"error":"nope"}')])

    with pytest.raises(ProviderError, match=f"status={status}"):
        azure(session).complete(system="s", user="u")

    assert len(session.calls) == 1


def test_the_error_body_survives_into_the_message():
    """The status code alone never explains a content filter or a quota."""
    session = FakeSession([FakeResponse(400, text='{"error":"content_filter"}')])

    with pytest.raises(ProviderError, match="content_filter"):
        azure(session).complete(system="s", user="u")


# --- request shape -----------------------------------------------------------


def test_azure_addresses_the_deployment_not_the_model():
    session = FakeSession([CHAT_OK])

    azure(session).complete(system="s", user="u")

    assert "/deployments/chat/chat/completions" in session.calls[0]["url"]
    assert session.calls[0]["headers"]["api-key"] == "k"


def test_gemini_sends_the_key_in_a_header_not_the_query_string():
    """Query strings end up in proxy and server logs."""
    session = FakeSession([GEMINI_OK])

    gemini(session).complete(system="s", user="u")

    call = session.calls[0]
    assert call["headers"]["x-goog-api-key"] == "k"
    assert "key=" not in call["url"]


def test_gemini_keeps_the_system_prompt_out_of_the_conversation_turns():
    """So retrieved documents cannot pose as instructions."""
    session = FakeSession([GEMINI_OK])

    gemini(session).complete(system="reglas", user="pregunta")

    payload = session.calls[0]["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "reglas"
    assert payload["contents"][0]["parts"][0]["text"] == "pregunta"


# --- degenerate answers ------------------------------------------------------


def test_a_gemini_answer_blocked_by_safety_becomes_an_error():
    """Returning "" would look like a model with nothing to say."""
    session = FakeSession([FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}})])

    with pytest.raises(ProviderError, match="SAFETY"):
        gemini(session).complete(system="s", user="u")


def test_an_answer_truncated_before_any_text_becomes_an_error():
    """A reasoning model can spend the whole budget thinking and emit nothing."""
    session = FakeSession(
        [FakeResponse(200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]})]
    )

    with pytest.raises(ProviderError, match="MAX_TOKENS"):
        gemini(session).complete(system="s", user="u")


def test_an_answer_truncated_after_some_text_becomes_an_error():
    """El fallo real que se vio contra Vertex, y que el código dejaba pasar.

    El razonamiento se cobra contra ``maxOutputTokens``: gastó 1962 de 2048
    pensando y devolvió 82 de JSON, cortado a media frase. Como sí había texto,
    volvía arriba y el guardarraíl lo rechazaba por "no es JSON válido" — un
    mensaje que manda a revisar el prompt cuando lo que faltaron fueron tokens.
    """
    truncada = FakeResponse(200, {"candidates": [{
        "finishReason": "MAX_TOKENS",
        "content": {"parts": [{"text": '```json\n{"respuesta": "El deducible es de $5,0'}]},
    }]})
    session = FakeSession([truncada])

    with pytest.raises(ProviderError, match="MAX_TOKENS"):
        gemini(session).complete(system="s", user="u")


def test_thinking_never_gets_the_whole_budget():
    """Sin tope, pensar deja a la respuesta sin sitio y el JSON vuelve cortado.

    El reparto es una fracción y no una constante porque las llamadas no piden
    lo mismo: al clasificador le bastan 512 en total, y un presupuesto fijo de
    razonamiento se los comería enteros.
    """
    for max_tokens, esperado in ((2048, 512), (3000, 512), (512, 128)):
        session = FakeSession([GEMINI_OK])

        gemini(session).complete(system="s", user="u", max_tokens=max_tokens)

        config = session.calls[0]["json"]["generationConfig"]
        assert config["thinkingConfig"]["thinkingBudget"] == esperado
        assert config["thinkingConfig"]["thinkingBudget"] < config["maxOutputTokens"]


def test_embeddings_come_back_in_the_order_they_were_asked_for():
    session = FakeSession(
        [FakeResponse(200, {"data": [
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ]})]
    )

    assert azure(session).embed(["a", "b"]) == [[0.1], [0.2]]


# --- what the server asks for ------------------------------------------------


def test_a_short_retry_after_is_honoured(monkeypatch):
    """The server knows better than the backoff formula how long it needs."""
    esperas = []
    monkeypatch.setattr("app.providers.http.time.sleep", esperas.append)
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "7"}), CHAT_OK])

    azure(session).complete(system="s", user="u")

    assert esperas == [7.0]


def test_googles_retry_delay_dialect_is_read(monkeypatch):
    esperas = []
    monkeypatch.setattr("app.providers.http.time.sleep", esperas.append)
    body = {"error": {"details": [{"retryDelay": "9s"}]}}
    session = FakeSession([FakeResponse(429, payload=body), GEMINI_OK])

    gemini(session).complete(system="s", user="u")

    assert esperas == [9.0]


def test_a_daily_quota_is_terminal_not_something_to_wait_out():
    """Retrying it burns what little quota is left and delays the escalation."""
    body = {"error": {"details": [{"retryDelay": "3600s"}]}}
    session = FakeSession([FakeResponse(429, payload=body, text="quota exceeded")])

    with pytest.raises(ProviderError, match="rate limited for 3600s"):
        gemini(session).complete(system="s", user="u")

    assert len(session.calls) == 1


def test_without_a_hint_the_backoff_grows(monkeypatch):
    esperas = []
    monkeypatch.setattr("app.providers.http.time.sleep", esperas.append)
    session = FakeSession([FakeResponse(503) for _ in range(4)])

    with pytest.raises(ProviderError):
        azure(session).complete(system="s", user="u")

    # base 1.0 con jitter completo: el intento n espera en [2^(n-1), 2^n]
    assert 1.0 <= esperas[0] <= 2.0
    assert 2.0 <= esperas[1] <= 4.0
    assert 4.0 <= esperas[2] <= 8.0


# --- Vertex AI: la misma interfaz, otra forma de llegar ----------------------


class FakeCredentials:
    """Una credencial de cuenta de servicio, sin firmar nada de verdad."""

    def __init__(self, valid=True, project_id="proyecto-del-json"):
        self.valid = valid
        self.project_id = project_id
        self.token = "token-de-acceso"
        self.refrescos = 0

    def refresh(self, _request):
        self.refrescos += 1
        self.valid = True
        self.token = "token-renovado"


def vertex(session, credentials=None, **overrides):
    from app.config import VertexSettings
    from app.providers.vertex_ai import VertexAIProvider

    kwargs = {
        "credentials_path": Path("no-se-lee.json"),
        "project": "",
        "location": "global",
        "chat_model": "gemini-2.5-flash",
        "embedding_model": "gemini-embedding-001",
    }
    kwargs.update(overrides)

    provider = VertexAIProvider.__new__(VertexAIProvider)
    provider._credentials = credentials or FakeCredentials()
    provider._project = kwargs["project"] or provider._credentials.project_id
    provider._location = kwargs["location"]
    provider._settings = VertexSettings(**kwargs)
    provider._timeout = 30.0
    provider._session = session
    return provider


VERTEX_EMB_OK = FakeResponse(
    200, {"predictions": [{"embeddings": {"values": [0.1, 0.2]}},
                          {"embeddings": {"values": [0.3, 0.4]}}]}
)


def test_vertex_authenticates_with_a_bearer_token_not_a_key():
    """El punto entero del canal: nada estático que se pueda copiar."""
    session = FakeSession([GEMINI_OK])

    vertex(session).complete(system="s", user="u")

    cabeceras = session.calls[0]["headers"]
    assert cabeceras["Authorization"] == "Bearer token-de-acceso"
    assert "api-key" not in cabeceras
    assert "x-goog-api-key" not in cabeceras


def test_an_expired_token_is_refreshed_before_the_call():
    credenciales = FakeCredentials(valid=False)
    session = FakeSession([GEMINI_OK])

    vertex(session, credenciales).complete(system="s", user="u")

    assert credenciales.refrescos == 1
    assert session.calls[0]["headers"]["Authorization"] == "Bearer token-renovado"


def test_a_valid_token_is_not_refreshed_again():
    credenciales = FakeCredentials(valid=True)
    session = FakeSession([GEMINI_OK, GEMINI_OK])

    proveedor = vertex(session, credenciales)
    proveedor.complete(system="s", user="u")
    proveedor.complete(system="s", user="u")

    assert credenciales.refrescos == 0


def test_the_project_comes_from_the_credential_when_none_is_configured():
    """Elimina una clase entera de 403 confusos: credencial válida, proyecto ajeno."""
    session = FakeSession([GEMINI_OK])

    vertex(session).complete(system="s", user="u")

    assert "/projects/proyecto-del-json/" in session.calls[0]["url"]


def test_an_explicit_project_wins_over_the_credential():
    session = FakeSession([GEMINI_OK])

    vertex(session, project="otro-proyecto").complete(system="s", user="u")

    assert "/projects/otro-proyecto/" in session.calls[0]["url"]


def test_global_is_not_a_region_so_it_gets_no_regional_host():
    session = FakeSession([GEMINI_OK])

    vertex(session, location="global").complete(system="s", user="u")

    assert session.calls[0]["url"].startswith("https://aiplatform.googleapis.com/")
    assert "/locations/global/" in session.calls[0]["url"]


def test_a_region_gets_its_regional_host():
    session = FakeSession([GEMINI_OK])

    vertex(session, location="us-central1").complete(system="s", user="u")

    assert session.calls[0]["url"].startswith("https://us-central1-aiplatform.googleapis.com/")


def test_vertex_embeddings_use_predict_with_instances():
    """Otro endpoint, otro payload y otra forma de respuesta que la de AI Studio."""
    session = FakeSession([VERTEX_EMB_OK])

    assert vertex(session).embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]

    llamada = session.calls[0]
    assert llamada["url"].endswith(":predict")
    assert llamada["json"] == {"instances": [{"content": "a"}, {"content": "b"}]}


def test_a_vertex_answer_truncated_before_any_text_becomes_an_error():
    session = FakeSession(
        [FakeResponse(200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]})]
    )

    with pytest.raises(ProviderError, match="MAX_TOKENS"):
        vertex(session).complete(system="s", user="u")


def test_with_no_credentials_at_all_it_refuses_to_start(monkeypatch):
    """Sin archivo Y sin identidad en el entorno no hay con qué autenticarse.

    Antes bastaba con no tener archivo. Ya no: no tenerlo es lo normal en Cloud
    Run, donde la identidad la da la cuenta adjunta. Lo que sigue siendo un
    error es que no haya ninguna de las dos.
    """
    import google.auth

    from app.config import VertexSettings
    from app.providers.vertex_ai import VertexAIProvider

    def sin_credenciales(scopes=None):
        raise google.auth.exceptions.DefaultCredentialsError("no hay")

    monkeypatch.setattr(google.auth, "default", sin_credenciales)

    with pytest.raises(ProviderError, match="no está configurado"):
        VertexAIProvider(VertexSettings(None, "", "global", "m", "e"))


# --- credenciales: archivo o identidad adjunta -------------------------------


def test_a_configured_path_that_does_not_exist_is_still_an_error(tmp_path):
    """Una errata en la ruta no puede acabar hablando con Vertex como otra cuenta.

    Desde que existe el camino de credenciales por defecto, caer en él cuando
    alguien SÍ configuró un archivo silenciaría el error y usaría la identidad
    del entorno. Distinto de no configurar nada, que es el caso de Cloud Run.
    """
    from app.config import VertexSettings
    from app.providers.vertex_ai import VertexAIProvider

    ajustes = VertexSettings(
        credentials_path=tmp_path / "no-existe.json",
        project="proyecto",
        location="us-central1",
        chat_model="m",
        embedding_model="e",
    )

    with pytest.raises(ProviderError, match="no service account file"):
        VertexAIProvider(ajustes)


def test_with_no_path_configured_it_uses_the_ambient_identity(monkeypatch):
    """Es como se despliega en Cloud Run: la cuenta va adjunta al servicio.

    Meter un JSON de credenciales en la imagen lo deja en una capa para siempre
    y hay que rotarlo a mano; con la cuenta adjunta no existe llave que filtrar.
    """
    import google.auth

    from app.config import VertexSettings
    from app.providers.vertex_ai import VertexAIProvider

    class CredencialFalsa:
        valid = True
        token = "ficticio"

    monkeypatch.setattr(
        google.auth, "default", lambda scopes=None: (CredencialFalsa(), "proyecto-adc")
    )

    ajustes = VertexSettings(
        credentials_path=None, project="", location="us-central1",
        chat_model="m", embedding_model="e",
    )

    proveedor = VertexAIProvider(ajustes)

    assert proveedor._project == "proyecto-adc"  # noqa: SLF001 - se comprueba el cableado
