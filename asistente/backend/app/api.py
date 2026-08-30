"""FastAPI surface.

Endpoints for asking, and for the documents the answers come from. The one
thing worth noticing here is that the customer identity comes from the session,
never from the request body — a client that could name its own customer id
could name someone else's. Every route derives scope from that identity alone.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import sugerencias as sugerencias_module
from app.assistant import Assistant
from app.conversaciones import Conversaciones, formatear
from app.escalamientos import Escalamientos
from app.auth import (
    AuthError,
    Identity,
    identity_from_claims,
    issue_session,
    read_session,
    verify_google_token,
)
from app.config import (
    AUTH_GOOGLE,
    AUTH_LOCAL,
    BACKEND_POSTGRES,
    Settings,
    configure_logging,
    load_settings,
)
from app.ingest import IngestError, parse_upload
from app.pdf import MEDIO_PDF, markdown_a_pdf
from app.invoices import InvoiceStore
from app.providers import ProviderError, build_provider
from app.retrieval import SCOPE_CUSTOMER, Retriever, load_corpus
from app.store import ORIGIN_CORPUS, PostgresVectorStore, StoreError
from app.titulos import limpiar as acortar_titulo
from app.usuarios import RepositorioUsuarios, hash_contrasena

logger = logging.getLogger(__name__)

# Stand-in for the real identity provider. In production this is the validated
# Entra ID token; here it maps a demo header to a customer. The point of keeping
# it explicit is that the rest of the code never has to care which one it is.
DEMO_CUSTOMERS = {
    "logistica": "kayelo3614@neowd.com",
    "meridiano": "finanzas@meridiano.mx",
    "aurora": "pagos@aurora.mx",
    "zenit": "contabilidad@zenit.mx",
}


# Los cuatro clientes de demostración. En `local` se siembran como usuarios
# reales de la tabla; en `demo` siguen siendo la cabecera.
DEMO_NOMBRES = {
    "kayelo3614@neowd.com": "Logistica Pacifico",
    "finanzas@meridiano.mx": "Grupo Meridiano",
    "pagos@aurora.mx": "Comercial Aurora",
    "contabilidad@zenit.mx": "Constructora Zenit",
}


class Question(BaseModel):
    pregunta: str = Field(min_length=1, max_length=4000)
    # Identificador de conversación. El cliente lo repite; qué se dijo lo sabe
    # el servidor. Un historial que escribe el cliente se puede inventar.
    conversacion: str = Field(default="", max_length=64)
    # El documento que la persona tiene seleccionado, si hay alguno. Nombra un
    # documento, no una identidad: el almacén sigue comprobando si puede verlo.
    documento: str = Field(default="", max_length=400)
    # El cliente pide una persona. Es una petición explícita, no una deducción
    # del asistente: el motivo del caso lo dirá así.
    humano: bool = False


class Credenciales(BaseModel):
    correo: str = Field(min_length=3, max_length=254)
    contrasena: str = Field(min_length=1, max_length=200)


class Valoracion(BaseModel):
    turno: int = Field(gt=0)
    util: bool
    comentario: str = Field(default="", max_length=1000)


class Contacto(BaseModel):
    contacto: str = Field(default="", max_length=200)
    nota: str = Field(default="", max_length=1000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Asistente de pólizas y facturación",
        version="2.0.0",
        description="Bloque 3 — RAG con permisos por cliente, pgvector y escalamiento.",
    )
    # The Vite dev server runs on another port, so the browser needs this.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    state: dict = {"settings": settings, "error": ""}

    @app.on_event("startup")
    def startup() -> None:
        """Build the provider and the retriever once, not per request.

        A store or provider that cannot start is not fatal: the app comes up and
        every question escalates, which is far more useful than a container that
        crash-loops and answers nothing at all.
        """
        try:
            provider = build_provider(settings)
        except ProviderError as exc:
            logger.error("Provider unavailable reason=%s", exc)
            state["error"] = str(exc)
            return

        try:
            retriever = _build_retriever(settings, provider, state)
        except (ProviderError, StoreError) as exc:
            logger.error("Retrieval unavailable reason=%s", exc)
            state["error"] = str(exc)
            return

        if settings.auth_mode == AUTH_LOCAL:
            try:
                state["usuarios"] = _preparar_usuarios(settings, state)
            except StoreError as exc:
                logger.error("Users unavailable reason=%s", exc)
                state["error"] = str(exc)
                return

        if settings.retrieval_backend == BACKEND_POSTGRES:
            memoria = Conversaciones(state["store"].connect)
            memoria.crear_esquema()
            state["memoria"] = memoria

            casos = Escalamientos(state["store"].connect)
            casos.crear_esquema()
            state["casos"] = casos

        state["provider"] = provider
        state["retriever"] = retriever
        state["assistant"] = Assistant(
            settings=settings,
            provider=provider,
            retriever=retriever,
            invoice_store=InvoiceStore(settings.invoices_path),
        )
        state["error"] = ""
        logger.info("Assistant ready provider=%s backend=%s",
                    provider.name, settings.retrieval_backend)

    def current_identity(
        x_demo_customer: str = Header(default="logistica"),
        authorization: str = Header(default=""),
    ) -> Identity:
        """Resolve the caller. Never from the request body, ever.

        In ``google`` mode the demo header is ignored outright — leaving it as
        a fallback would turn authentication into a suggestion.
        """
        if settings.auth_mode == AUTH_LOCAL:
            esquema, _, token = authorization.partition(" ")
            if esquema.lower() != "bearer" or not token.strip():
                raise HTTPException(status_code=401, detail="falta el token de sesión")
            try:
                correo = read_session(token.strip(), settings.session_secret)
            except AuthError as exc:
                raise HTTPException(status_code=401, detail="sesión no válida") from exc

            # El token demuestra quién entró; los permisos se releen de la base
            # en cada petición. Si una cuenta cambia de cliente o se desactiva,
            # surte efecto sin esperar a que caduque la sesión.
            repositorio: RepositorioUsuarios = state["usuarios"]
            usuario = repositorio.buscar(correo)
            if usuario is None:
                raise HTTPException(status_code=401, detail="sesión no válida")
            return Identity(
                email=usuario.correo, name=usuario.nombre, picture="", customer=usuario.cliente
            )

        if settings.auth_mode == AUTH_GOOGLE:
            esquema, _, token = authorization.partition(" ")
            if esquema.lower() != "bearer" or not token.strip():
                raise HTTPException(status_code=401, detail="falta el token de sesión")
            try:
                claims = verify_google_token(token.strip(), settings.google_client_id)
            except AuthError as exc:
                raise HTTPException(status_code=401, detail="sesión no válida") from exc
            return identity_from_claims(
                claims, settings.account_links, set(DEMO_CUSTOMERS.values())
            )

        email = DEMO_CUSTOMERS.get(x_demo_customer.strip().lower())
        if not email:
            raise HTTPException(status_code=401, detail="cliente no reconocido")
        return Identity(email=email, name=email, picture="", customer=email)

    def current_customer(identidad: Identity = Depends(current_identity)) -> str:
        """The customer account this caller may read.

        A verified account with no link is authenticated but authorises nothing
        beyond the public documents. Saying so plainly beats pretending the
        person does not exist.
        """
        if not identidad.linked:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{identidad.email} no está vinculado a ninguna cuenta de cliente. "
                    "Pide que la enlacen en ACCOUNT_LINKS."
                ),
            )
        return identidad.customer

    def require_store() -> PostgresVectorStore:
        """Uploads need a place to persist; the in-memory backend has none."""
        store = state.get("store")
        if store is None:
            raise HTTPException(
                status_code=409,
                detail="la carga de documentos requiere RETRIEVAL_BACKEND=postgres",
            )
        return store

    # --- estado ---------------------------------------------------------------

    @app.get("/api/salud")
    def health() -> dict:
        return {
            "proveedor": settings.provider,
            "almacen": settings.retrieval_backend,
            "autenticacion": settings.auth_mode,
            # El id de cliente de OAuth es público por diseño: va en el HTML de
            # cualquier página que use Google Sign-In. Lo secreto es el secret,
            # que este flujo no usa.
            "google_client_id": settings.google_client_id,
            "cargas": "store" in state,
            "listo": "assistant" in state,
            "error": state.get("error", ""),
        }

    @app.get("/api/sesion")
    def session(identidad: Identity = Depends(current_identity)) -> dict:
        """Quién eres y qué cuenta puedes leer. La UI se dibuja con esto."""
        return identidad.as_dict()

    @app.post("/api/acceso")
    def login(credenciales: Credenciales = Body(...)) -> dict:
        """Correo y contraseña contra la tabla ``usuarios``."""
        if settings.auth_mode != AUTH_LOCAL:
            raise HTTPException(status_code=409, detail="el acceso local no está activo")

        repositorio: RepositorioUsuarios | None = state.get("usuarios")
        if repositorio is None:
            raise HTTPException(status_code=503, detail="el directorio no está disponible")

        usuario = repositorio.autenticar(credenciales.correo, credenciales.contrasena)
        if usuario is None:
            # Un único mensaje para correo desconocido y contraseña incorrecta.
            # Distinguirlos convierte el formulario en un buscador de cuentas.
            logger.info("Failed login correo=%s", credenciales.correo.strip().lower()[:64])
            raise HTTPException(status_code=401, detail="correo o contraseña incorrectos")

        logger.info("Login correo=%s", usuario.correo)
        return {
            "token": issue_session(usuario.correo, settings.session_secret),
            "sesion": Identity(
                usuario.correo, usuario.nombre, "", usuario.cliente
            ).as_dict(),
        }

    @app.get("/api/clientes")
    def customers() -> dict:
        """The demo identities the UI offers, so the front end hardcodes nothing."""
        return {"clientes": [{"id": k, "correo": v} for k, v in DEMO_CUSTOMERS.items()]}

    # --- preguntas ------------------------------------------------------------

    def _responder(payload: Question, customer_email: str, avisar, empresa: str = "") -> dict:
        """El trabajo de una pregunta, sin depender de cómo se entregue.

        Lo usan las dos rutas: la de siempre, que devuelve un JSON al final, y la
        de etapas, que va contando por dónde va. Que compartan cuerpo es lo que
        garantiza que respondan lo mismo.
        """
        assistant: Assistant | None = state.get("assistant")
        if assistant is None:
            raise HTTPException(
                status_code=503,
                detail=state.get("error") or "el asistente no está inicializado",
            )

        memoria: Conversaciones | None = state.get("memoria")
        conversacion = payload.conversacion.strip()
        historial = ""
        documento = payload.documento.strip()

        if memoria is not None:
            if conversacion and memoria.pertenece(conversacion, customer_email):
                historial = formatear(memoria.recordar(conversacion, customer_email))
                # "Resúmelo" se resuelve con lo último citado, pero solo si la
                # persona no señaló un documento: lo explícito manda sobre lo
                # inferido, siempre.
                documento = documento or memoria.ultimo_documento(conversacion, customer_email)
            else:
                # Identificador ausente, inválido o de otra cuenta: se empieza
                # una conversación nueva en vez de fallar. Nadie pierde su
                # pregunta por un identificador caducado.
                conversacion = memoria.abrir(customer_email)

        respuesta = assistant.ask(
            payload.pregunta,
            customer_email,
            historial=historial,
            documento=documento,
            progreso=avisar,
            pedir_humano=payload.humano,
        )

        turno = 0
        if memoria is not None and conversacion:
            # Solo las fuentes documentales llevan `documento`; las de factura
            # no. Acceder por clave daba KeyError en cuanto la respuesta venía
            # de una factura, que es la mitad de las respuestas.
            citado = next(
                (f["documento"] for f in respuesta.sources if f.get("documento")), ""
            )
            turno = memoria.anotar(
                conversacion,
                customer_email,
                # La redactada, no la que llegó. Quitar un RFC del prompt y
                # dejarlo escrito en la tabla de turnos no protege de nada:
                # seguiría ahí en el respaldo, y volvería al prompt en cuanto
                # esa conversación se retomara.
                pregunta=respuesta.question or payload.pregunta,
                respuesta=respuesta.text,
                documento=respuesta.data.get("documento") or citado,
            )

        # El caso escalado se guarda de verdad. Hasta ahora el folio se escribía
        # en el log y se tiraba, con lo que la respuesta prometía un seguimiento
        # que no existía en ninguna parte.
        casos: Escalamientos | None = state.get("casos")
        if respuesta.escalated and casos is not None and respuesta.reference:
            casos.registrar(
                respuesta.reference,
                customer_email,
                intencion=respuesta.intent,
                pregunta=respuesta.question or payload.pregunta,
                motivo=respuesta.reason,
            )

        salida = respuesta.as_dict()

        # Los documentos que se ofrecen a elegir se nombran como en el resto de
        # la interfaz: sin el nombre de la empresa, que ya está en el encabezado.
        # La regla vive aquí y no en el asistente porque es de presentación: el
        # asistente trabaja con el título completo, que es el que identifica.
        opciones = salida.get("datos", {}).get("opciones")
        if opciones:
            for opcion in opciones:
                opcion["titulo"] = acortar_titulo(opcion["titulo"], empresa)

        salida["conversacion"] = conversacion
        # Con qué valorar *esta* respuesta. Sin él, un "no me sirvió" no diría a
        # cuál de seis respuestas se refiere.
        salida["turno"] = turno
        return salida

    @app.post("/api/preguntar")
    def ask(
        payload: Question = Body(...),
        customer_email: str = Depends(current_customer),
        identidad: Identity = Depends(current_identity),
    ) -> dict:
        return _responder(payload, customer_email, lambda _etapa: None, identidad.name)

    @app.post("/api/preguntar/flujo")
    def ask_stream(
        payload: Question = Body(...),
        customer_email: str = Depends(current_customer),
        identidad: Identity = Depends(current_identity),
    ):
        """Lo mismo, pero contando por dónde va.

        La ruta documental encadena clasificar, embeber, recuperar y redactar, y
        eso son entre tres y catorce segundos. Con una sola animación de puntos
        los catorce parecen una caída. Las etapas salen de donde de verdad
        ocurren —el asistente las emite— y no de un temporizador en el navegador,
        que acabaría anunciando "buscando en tus documentos" en una consulta ya
        terminada.

        El trabajo es síncrono, así que corre en un hilo y va dejando las etapas
        en una cola que el generador vacía. Sin el hilo no habría nada que
        emitir hasta el final, que es exactamente lo que se quiere evitar.
        """
        if state.get("assistant") is None:
            raise HTTPException(
                status_code=503,
                detail=state.get("error") or "el asistente no está inicializado",
            )

        etapas: queue.Queue = queue.Queue()
        resultado: dict = {}
        FIN = object()

        def trabajar() -> None:
            try:
                resultado["salida"] = _responder(
                    payload, customer_email, etapas.put, identidad.name
                )
            except HTTPException as exc:
                resultado["error"] = str(exc.detail)
            except Exception as exc:  # noqa: BLE001 - va al cliente como error
                logger.exception("Fallo respondiendo en flujo")
                resultado["error"] = str(exc)
            finally:
                etapas.put(FIN)

        hilo = threading.Thread(target=trabajar, daemon=True)
        hilo.start()

        def emitir():
            while True:
                etapa = etapas.get()
                if etapa is FIN:
                    break
                yield _evento("etapa", {"etapa": etapa})
            hilo.join()
            if "error" in resultado:
                yield _evento("error", {"detalle": resultado["error"]})
            else:
                yield _evento("respuesta", resultado["salida"])

        return StreamingResponse(
            emitir(),
            media_type="text/event-stream",
            # Sin esto un proxy que almacene en búfer entrega las etapas todas
            # juntas al final, que es como no haberlas mandado.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sugerencias")
    def suggestions(
        customer_email: str = Depends(current_customer),
        identidad: Identity = Depends(current_identity),
    ) -> dict:
        """Preguntas que ESTA cuenta puede hacer y obtener respuesta."""
        facturas = InvoiceStore(settings.invoices_path).for_customer(customer_email)
        documentos = []
        retriever = state.get("retriever")
        listar = getattr(retriever, "list_documents", None)
        if listar is not None:
            try:
                documentos = listar(customer_email)
            except StoreError as exc:
                logger.warning("No se pudieron listar documentos reason=%s", exc)
        return {
            "sugerencias": sugerencias_module.para_cliente(
                facturas, documentos, identidad.name
            )
        }

    @app.post("/api/valoracion")
    def rate(
        payload: Valoracion = Body(...),
        customer_email: str = Depends(current_customer),
    ) -> dict:
        """Si una respuesta sirvió o no.

        Todo el diseño va de callarse cuando no hay evidencia; faltaba la señal
        contraria — cuando respondió y se equivocó. Es el único dato que dice si
        el umbral de similitud está bien puesto, y no se estaba recogiendo.
        """
        memoria: Conversaciones | None = state.get("memoria")
        if memoria is None:
            raise HTTPException(status_code=409, detail="no hay memoria de conversación")
        if not memoria.valorar(
            payload.turno, customer_email, util=payload.util, comentario=payload.comentario
        ):
            raise HTTPException(status_code=404, detail="respuesta no encontrada")
        logger.info("Valoración turno=%s util=%s", payload.turno, payload.util)
        return {"turno": payload.turno, "util": payload.util}

    @app.get("/api/conversaciones")
    def conversations(
        customer_email: str = Depends(current_customer),
        identidad: Identity = Depends(current_identity),
    ) -> dict:
        memoria: Conversaciones | None = state.get("memoria")
        if memoria is None:
            return {"conversaciones": []}

        # El título se acorta al leer, no al guardar: así la pregunta original
        # sigue entera en la base —es lo que la persona escribió— y el acortado
        # alcanza también a las conversaciones que ya estaban guardadas.
        conversaciones = memoria.listar(customer_email)
        for conversacion in conversaciones:
            conversacion["titulo"] = acortar_titulo(
                conversacion["titulo"], identidad.name
            )
        return {"conversaciones": conversaciones}

    @app.get("/api/conversacion/{conversacion}")
    def conversation(
        conversacion: str, customer_email: str = Depends(current_customer)
    ) -> dict:
        """La conversación entera, para volver a abrirla."""
        memoria: Conversaciones | None = state.get("memoria")
        if memoria is None:
            raise HTTPException(status_code=409, detail="no hay memoria de conversación")
        if not memoria.pertenece(conversacion, customer_email):
            raise HTTPException(status_code=404, detail="conversación no encontrada")
        return {"id": conversacion, "turnos": memoria.transcribir(conversacion, customer_email)}

    @app.get("/api/escalamientos")
    def escalations(customer_email: str = Depends(current_customer)) -> dict:
        casos: Escalamientos | None = state.get("casos")
        if casos is None:
            return {"escalamientos": []}
        return {"escalamientos": [c.as_dict() for c in casos.listar(customer_email)]}

    @app.post("/api/escalamientos/{folio}/contacto")
    def escalation_contact(
        folio: str,
        payload: Contacto = Body(...),
        customer_email: str = Depends(current_customer),
    ) -> dict:
        """Cómo prefiere que le contacten, añadido a su propio caso."""
        casos: Escalamientos | None = state.get("casos")
        if casos is None:
            raise HTTPException(status_code=409, detail="no hay registro de escalamientos")
        if not casos.anotar_contacto(
            folio, customer_email, contacto=payload.contacto, nota=payload.nota
        ):
            raise HTTPException(status_code=404, detail="folio no encontrado")
        caso = casos.detallar(folio, customer_email)
        return caso.as_dict() if caso else {"folio": folio}

    @app.delete("/api/conversacion/{conversacion}")
    def forget(conversacion: str, customer_email: str = Depends(current_customer)) -> dict:
        """Olvidar la conversación. Que se pueda borrar es parte de recordar."""
        memoria: Conversaciones | None = state.get("memoria")
        if memoria is None:
            raise HTTPException(status_code=409, detail="no hay memoria de conversación")
        if not memoria.olvidar(conversacion, customer_email):
            raise HTTPException(status_code=404, detail="conversación no encontrada")
        return {"olvidada": conversacion}

    # --- documentos -----------------------------------------------------------

    @app.get("/api/documentos")
    def list_documents(
        customer_email: str = Depends(current_customer),
        identidad: Identity = Depends(current_identity),
    ) -> dict:
        store = require_store()
        try:
            documentos = [d.as_dict() for d in store.list_documents(customer_email)]
        except StoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # Mismo motivo que en el historial, y aquí duele más: con el ancho de la
        # barra lateral, "Carátula de póliza — Grupo Meridiano" se recortaba en
        # "Carátula de póliza — G…", tapando lo único que distingue un documento
        # de otro para enseñar el nombre que está repetido en todas las filas.
        # `nombre` no se toca: es la clave con la que se piden y se descargan.
        for documento in documentos:
            documento["titulo"] = acortar_titulo(documento["titulo"], identidad.name)
        return {"documentos": documentos}

    @app.post("/api/documentos")
    async def upload_document(
        archivo: UploadFile = File(...),
        conversacion: str = Form(default=""),
        customer_email: str = Depends(current_customer),
    ) -> dict:
        store = require_store()
        raw = await archivo.read()

        try:
            parsed = parse_upload(archivo.filename or "documento.txt", raw, customer_email)
        except IngestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            # The scope is decided here, never taken from the request. An
            # uploader who could pick `publico` could publish to every customer.
            fragmentos = store.upsert_document(
                nombre=parsed.nombre,
                titulo=parsed.titulo,
                alcance=SCOPE_CUSTOMER,
                cliente=customer_email,
                origen="carga",
                textos=parsed.fragmentos,
                archivo=parsed.archivo,
                medio=parsed.medio,
            )
        except (StoreError, ProviderError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        # El documento recién subido pasa a ser del que se habla. Sin esto,
        # subir un archivo y preguntar "¿de qué trata?" devolvía el resumen del
        # documento ANTERIOR: la conversación solo sabía de documentos citados
        # en una respuesta, y una carga no es una respuesta. Subir algo es decir
        # "hablemos de esto" con más claridad que cualquier pronombre.
        #
        # Si aún no hay conversación se abre una. El caso más común es
        # justamente ese —entrar, subir un archivo y preguntar por él— y ahí el
        # identificador todavía no existe, porque hasta ahora solo lo creaba la
        # primera pregunta. Una conversación sin turnos no aparece en el
        # historial, así que abrirla de más no ensucia nada.
        hilo = conversacion.strip()
        memoria: Conversaciones | None = state.get("memoria")
        if memoria is not None:
            if not hilo or not memoria.pertenece(hilo, customer_email):
                hilo = memoria.abrir(customer_email)
            memoria.fijar_documento(hilo, customer_email, parsed.nombre)

        logger.info("Upload stored cliente=%s nombre=%s fragmentos=%d",
                    customer_email, parsed.nombre, fragmentos)
        return {
            "nombre": parsed.nombre,
            "titulo": parsed.titulo,
            "fragmentos": fragmentos,
            "conversacion": hilo,
        }

    @app.get("/api/documentos/{nombre:path}/archivo")
    def download_document(nombre: str, customer_email: str = Depends(current_customer)):
        """Devuelve el archivo original, si este cliente puede verlo."""
        store = require_store()
        try:
            resultado = store.read_document(nombre, customer_email)
        except StoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if resultado is None:
            # La misma respuesta que si no existiera. Distinguir "no es tuyo" de
            # "no existe" confirmaría el documento de otro cliente.
            raise HTTPException(status_code=404, detail="documento no encontrado")

        titulo, archivo, medio = resultado
        if not archivo:
            # Este sí es suyo y lo está viendo en su lista, así que merece saber
            # por qué no baja en vez de un 404 mudo.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"«{titulo}» se indexó antes de que se guardara el archivo original. "
                    "Vuelve a subirlo para poder descargarlo."
                ),
            )
        return Response(
            content=archivo,
            media_type=medio,
            # `inline` y no `attachment`: el navegador abre el PDF en su propio
            # visor y quien quiera guardarlo lo hace desde ahí. Forzar la
            # descarga obligaría a bajar el archivo solo para leerlo.
            headers={"Content-Disposition": _adjunto(nombre, medio)},
        )

    @app.delete("/api/documentos/{nombre:path}")
    def delete_document(nombre: str, customer_email: str = Depends(current_customer)) -> dict:
        store = require_store()
        try:
            borrado = store.delete_document(nombre, customer_email)
        except StoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not borrado:
            # Same answer whether it never existed or belongs to someone else:
            # distinguishing them would confirm another customer's document.
            raise HTTPException(status_code=404, detail="documento no encontrado")
        return {"eliminado": nombre}

    _servir_frontend(app, settings)
    return app


def _servir_frontend(app: FastAPI, settings: Settings) -> None:
    """Sirve la interfaz ya compilada desde la propia API, si está presente.

    En desarrollo no lo está: manda Vite, con su recarga en caliente, y esto no
    hace nada. En un contenedor sí, y entonces backend e interfaz son un único
    servicio y un único origen. Eso quita el CORS —que existía solo porque Vite
    vive en otro puerto—, quita una URL que configurar, y quita la posibilidad
    de desplegar una mitad y olvidarse de la otra.
    """
    dist = settings.static_path
    if not dist or not (dist / "index.html").is_file():
        logger.info("Sin interfaz compilada en %s; solo se sirve la API", dist)
        return

    @app.get("/{camino:path}", include_in_schema=False)
    def spa(camino: str):
        """Cualquier ruta que no sea de la API devuelve la aplicación.

        Se declara al final, así que las rutas `/api/...` ya están registradas y
        ganan por orden. Aun así se comprueba el prefijo: si un día alguien
        pregunta por `/api/algo` que no existe, debe recibir un 404 de API y no
        una página HTML que su cliente no sabe leer.
        """
        if camino.startswith("api/"):
            raise HTTPException(status_code=404, detail="ruta no encontrada")

        pedido = (dist / camino).resolve() if camino else None
        # `is_relative_to` corta el "../../etc/passwd": sin esa comprobación,
        # un camino con saltos serviría cualquier archivo de la imagen.
        if pedido and pedido.is_file() and pedido.is_relative_to(dist.resolve()):
            return FileResponse(pedido)
        return FileResponse(dist / "index.html")

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    logger.info("Sirviendo la interfaz compilada desde %s", dist)


def _evento(nombre: str, datos: dict) -> str:
    """Un evento SSE.

    El JSON va en una sola línea porque el formato separa los eventos con una
    línea en blanco: un salto de línea sin escapar dentro del `data:` partiría
    el evento en dos y el cliente leería medio mensaje. `json.dumps` los escapa,
    y `ensure_ascii=False` deja pasar los acentos tal cual.
    """
    return f"event: {nombre}\ndata: {json.dumps(datos, ensure_ascii=False)}\n\n"


def _build_retriever(settings: Settings, provider, state: dict):
    """Either the in-memory retriever or the Postgres store, seeded once."""
    if settings.retrieval_backend != BACKEND_POSTGRES:
        chunks = load_corpus(settings.corpus_path)
        retriever = Retriever(
            chunks, provider, top_k=settings.top_k, min_similarity=settings.min_similarity
        )
        retriever.index()
        return retriever

    store = PostgresVectorStore(
        settings.database_url,
        provider,
        top_k=settings.top_k,
        min_similarity=settings.min_similarity,
    )
    store.ensure_ready()
    seed_corpus(store, settings)
    state["store"] = store
    return store


def seed_corpus(store: PostgresVectorStore, settings: Settings) -> int:
    """Carga los documentos del repositorio que aún no estén en el almacén.

    Documento a documento, y no todo-o-nada: la versión anterior se saltaba la
    siembra entera en cuanto existía un solo documento del corpus, así que
    añadir un archivo nuevo no lo indexaba nunca. Así solo se embebe lo que
    falta, que es lo que cuesta dinero.
    """
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre FROM documentos WHERE origen = %s", (ORIGIN_CORPUS,))
        ya_estan = {fila[0] for fila in cur.fetchall()}

    total = 0
    for chunk_group in _group_by_document(load_corpus(settings.corpus_path)):
        primero = chunk_group[0]
        if primero.document in ya_estan:
            continue
        store.upsert_document(
            nombre=primero.document,
            titulo=primero.title,
            alcance=primero.scope,
            cliente=primero.customer,
            origen=ORIGIN_CORPUS,
            textos=[c.text for c in chunk_group],
            archivo=markdown_a_pdf(
                (settings.corpus_path / primero.document).read_text(encoding="utf-8"),
                primero.title,
            ),
            medio=MEDIO_PDF,
        )
        total += 1

    _rellenar_contenido(store, settings)
    if total:
        logger.info("Corpus seeded documentos_nuevos=%d", total)
    return total


def _group_by_document(chunks) -> list[list]:
    agrupados: dict[str, list] = {}
    for chunk in chunks:
        agrupados.setdefault(chunk.document, []).append(chunk)
    return list(agrupados.values())


app = create_app()


def _preparar_usuarios(settings: Settings, state: dict) -> RepositorioUsuarios:
    """Crea la tabla y siembra los usuarios de demostración si está vacía.

    Sembrar solo cuando no hay nadie: si la tabla ya tiene gente, volver a
    escribirla restablecería contraseñas que alguien pudo haber cambiado.
    """
    store: PostgresVectorStore | None = state.get("store")
    conectar = store.connect if store else PostgresVectorStore(
        settings.database_url, None
    ).connect

    repositorio = RepositorioUsuarios(conectar)
    repositorio.crear_esquema()

    if repositorio.contar() == 0:
        for correo, nombre in DEMO_NOMBRES.items():
            # Un hash por usuario, no uno reutilizado: la sal por usuario es lo
            # que impide que dos filas con la misma contraseña se delaten entre
            # sí, y sembrarlas con el mismo hash tiraría esa propiedad.
            repositorio.alta(correo, nombre, correo, hash_contrasena(settings.seed_password))
        logger.warning(
            "Se sembraron %d usuarios de demostración con una contraseña compartida. "
            "Cámbiala con SEED_PASSWORD antes de usar esto para algo real.",
            len(DEMO_NOMBRES),
        )
    return repositorio


def _adjunto(nombre: str, medio: str = MEDIO_PDF) -> str:
    """Cabecera ``Content-Disposition`` con el nombre de archivo.

    El nombre viene de algo que subió una persona, así que no puede entrar tal
    cual en una cabecera: unas comillas o un salto de línea la partirían en dos
    y permitirían inyectar cabeceras. Se manda una versión ASCII saneada y, en
    ``filename*``, la real codificada según RFC 5987.
    """
    base = nombre.rsplit("/", 1)[-1] or "documento.pdf"
    if medio == MEDIO_PDF and not base.lower().endswith(".pdf"):
        # Los documentos del corpus se escriben en Markdown y se entregan como
        # PDF; el nombre tiene que decir lo que de verdad se descarga.
        base = base.rsplit(".", 1)[0] + ".pdf"

    ascii_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", base) or "documento.pdf"
    disposicion = "inline" if medio == MEDIO_PDF else "attachment"
    return (
        f'{disposicion}; filename="{ascii_seguro}"; '
        f"filename*=UTF-8''{quote(base, safe='')}"
    )


def _rellenar_contenido(store: PostgresVectorStore, settings: Settings) -> None:
    """Genera el PDF de documentos del corpus sembrados antes de guardarlo.

    Solo toca las columnas nuevas: volver a embeber un corpus que no ha cambiado
    costaría dinero por añadir una columna, que es un mal intercambio.
    """
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT nombre, titulo FROM documentos "
            "WHERE origen = %s AND length(archivo) = 0",
            (ORIGIN_CORPUS,),
        )
        pendientes = cur.fetchall()

        for nombre, titulo in pendientes:
            fuente = settings.corpus_path / nombre
            if not fuente.is_file():
                continue
            cur.execute(
                "UPDATE documentos SET archivo = %s, medio = %s WHERE nombre = %s",
                (markdown_a_pdf(fuente.read_text(encoding="utf-8"), titulo), MEDIO_PDF, nombre),
            )
        if pendientes:
            conn.commit()
            logger.info("Rendered corpus PDFs documentos=%d", len(pendientes))
