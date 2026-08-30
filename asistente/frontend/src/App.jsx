import { useEffect, useRef, useState } from 'react'
import Message from './Message.jsx'
import Documentos from './Documentos.jsx'
import Historial from './Historial.jsx'
import Acceso from './Acceso.jsx'
import Evidencia from './Evidencia.jsx'
import { useDocumentos } from './useDocumentos.js'
import { useTema } from './useTema.js'
import Tema from './Tema.jsx'
import {
  IconoAdjuntar,
  IconoCerrar,
  IconoDocumento,
  IconoEnviar,
  IconoEscudo,
  IconoEstado,
  IconoPregunta,
  IconoSalir,
} from './icons.jsx'
import { leerFlujo } from './flujo.js'

const EXTENSIONES = '.pdf,.md,.txt,.markdown'

export default function App() {
  const [pregunta, setPregunta] = useState('')
  const [mensajes, setMensajes] = useState([])
  const [cargando, setCargando] = useState(false)
  // En qué etapa va la consulta. Lo dice el servidor conforme ocurre; un
  // temporizador en el navegador acabaría anunciando "buscando en tus
  // documentos" en una consulta que ya había terminado.
  const [etapa, setEtapa] = useState('')
  const [sugerencias, setSugerencias] = useState([])
  const [conversaciones, setConversaciones] = useState([])
  const [verSugerencias, setVerSugerencias] = useState(false)
  const [salud, setSalud] = useState(null)
  // El token vive en memoria, no en localStorage: así no sobrevive a la
  // pestaña ni queda al alcance de cualquier script de la página.
  const [token, setToken] = useState(null)
  const [sesion, setSesion] = useState(null)
  const [errorAcceso, setErrorAcceso] = useState('')
  const [entrando, setEntrando] = useState(false)
  const { tema, alternar: alternarTema } = useTema()
  const [evidencia, setEvidencia] = useState(null)
  // El identificador de la conversación; el contenido lo guarda el servidor.
  const [conversacion, setConversacion] = useState('')
  const [documentoActivo, setDocumentoActivo] = useState(null)
  // Solo cuenta por debajo de 900px, donde el lateral es un panel que se abre.
  const [ladoAbierto, setLadoAbierto] = useState(false)
  const finRef = useRef(null)
  const entradaRef = useRef(null)
  const archivoRef = useRef(null)

  const { documentos, subiendo, subir, descargar, eliminar } = useDocumentos({
    token,
    disponible: Boolean(salud?.cargas),
  })

  // Una sola función construye las cabeceras de identidad, para que ninguna
  // llamada se quede sin ellas por descuido.
  function cabeceras(extra = {}) {
    return token
      ? { ...extra, Authorization: `Bearer ${token}` }
      : { ...extra, 'X-Demo-Customer': 'logistica' }
  }

  /** Correo y contraseña contra la tabla de usuarios. */
  async function entrar(correo, contrasena) {
    setEntrando(true)
    setErrorAcceso('')
    try {
      const r = await fetch('/api/acceso', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ correo, contrasena }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || 'no se pudo iniciar sesión')
      // El token vive en memoria, no en localStorage: así no sobrevive a la
      // pestaña ni queda al alcance de cualquier script de la página.
      setToken(d.token)
      setSesion(d.sesion)
    } catch (e) {
      setErrorAcceso(e.message)
    } finally {
      setEntrando(false)
    }
  }

  useEffect(() => {
    fetch('/api/salud')
      .then((r) => r.json())
      .then(setSalud)
      .catch(() => setSalud({ listo: false, error: 'backend no disponible' }))
  }, [])

  // Con el token en mano, preguntamos al backend quién dice que somos.
  useEffect(() => {
    if (!token || sesion) return
    fetch('/api/sesion', { headers: { Authorization: `Bearer ${token}` } })
      .then(async (r) => {
        const d = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(d.detail || 'sesión no válida')
        return d
      })
      .then((d) => {
        setSesion(d)
        setErrorAcceso(
          d.vinculado
            ? ''
            : `${d.correo} no está vinculado a ninguna cuenta de cliente.`,
        )
      })
      .catch((e) => {
        setToken(null)
        setErrorAcceso(e.message)
      })
  }, [token])

  useEffect(() => {
    finRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [mensajes, cargando])

  // Las sugerencias las construye el servidor con el expediente de la cuenta.
  // Escritas a mano en el cliente, tres de las seis escalaban.
  useEffect(() => {
    if (!token) return
    fetch('/api/sugerencias', { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : { sugerencias: [] }))
      .then((d) => setSugerencias(d.sugerencias || []))
      .catch(() => {})
    cargarConversaciones()
  }, [token])

  // Escape cierra el panel. Es lo que intenta cualquiera antes de buscar la X.
  useEffect(() => {
    if (!ladoAbierto) return
    const alPulsar = (e) => e.key === 'Escape' && setLadoAbierto(false)
    window.addEventListener('keydown', alPulsar)
    return () => window.removeEventListener('keydown', alPulsar)
  }, [ladoAbierto])

  async function preguntar(texto) {
    const q = (texto ?? pregunta).trim()
    if (!q || cargando) return

    setMensajes((m) => [...m, { rol: 'cliente', texto: q }])
    setPregunta('')
    setCargando(true)

    try {
      const respuesta = await fetch('/api/preguntar/flujo', {
        method: 'POST',
        headers: cabeceras({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          pregunta: q,
          conversacion,
          // Lo que la persona señaló manda sobre lo que el servidor infiera.
          documento: documentoActivo?.nombre || '',
        }),
      })
      if (!respuesta.ok) {
        const detalle = await respuesta.json().catch(() => ({}))
        throw new Error(detalle.detail || `error ${respuesta.status}`)
      }
      const datos = await leerFlujo(respuesta, { onEtapa: setEtapa })
      // El servidor devuelve el identificador incluso cuando abre una
      // conversación nueva, así que el siguiente turno ya tiene contexto.
      if (datos.conversacion) setConversacion(datos.conversacion)
      setMensajes((m) => [...m, { rol: 'asistente', ...datos }])
      cargarConversaciones()
    } catch (error) {
      // Un fallo de red se muestra como lo que es. Inventar una respuesta aquí
      // sería exactamente lo que el backend se cuida de no hacer.
      setMensajes((m) => [
        ...m,
        { rol: 'error', texto: `No se pudo consultar al asistente: ${error.message}` },
      ])
    } finally {
      setCargando(false)
      setEtapa('')
      setVerSugerencias(false)
      // Devolver el foco al campo: quien navega con teclado no debería tener
      // que volver a tabular hasta aquí después de cada pregunta.
      entradaRef.current?.focus()
    }
  }

  /**
   * La persona eligió de qué documento hablaba.
   *
   * Se marca como documento activo y se vuelve a preguntar en su nombre: así el
   * hilo queda con la pregunta original, la aclaración y la respuesta, en vez de
   * con un callejón sin salida seguido de otra pregunta suelta.
   */
  function elegirDocumento(opcion) {
    setDocumentoActivo({ nombre: opcion.nombre, titulo: opcion.titulo })
    preguntar(`Resume «${opcion.titulo}»`)
  }

  /**
   * La persona pide un ejecutivo.
   *
   * Va por el mismo camino que una pregunta —con su turno en el hilo y su folio—
   * pero marcado como petición explícita, para que quien reciba el caso sepa que
   * lo pidió el cliente y no que el asistente se rindió.
   */
  async function pedirHumano() {
    if (cargando) return
    setMensajes((m) => [...m, { rol: 'cliente', texto: 'Prefiero que lo vea una persona.' }])
    setCargando(true)
    try {
      const r = await fetch('/api/preguntar', {
        method: 'POST',
        headers: cabeceras({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          pregunta: 'Prefiero que lo vea una persona.',
          conversacion,
          humano: true,
        }),
      })
      if (!r.ok) throw new Error(`error ${r.status}`)
      const datos = await r.json()
      if (datos.conversacion) setConversacion(datos.conversacion)
      setMensajes((m) => [...m, { rol: 'asistente', ...datos }])
    } catch (error) {
      setMensajes((m) => [
        ...m,
        { rol: 'error', texto: `No se pudo abrir el caso: ${error.message}` },
      ])
    } finally {
      setCargando(false)
    }
  }

  /** Si la respuesta sirvió. Falla en silencio: no es la tarea de la persona. */
  async function valorar(turno, util, comentario) {
    await fetch('/api/valoracion', {
      method: 'POST',
      headers: cabeceras({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ turno, util, comentario }),
    }).catch(() => {})
  }

  /** Cómo prefiere que le contacten, añadido a su caso escalado. */
  async function enviarContacto(folio, contacto, nota) {
    try {
      const r = await fetch(`/api/escalamientos/${encodeURIComponent(folio)}/contacto`, {
        method: 'POST',
        headers: cabeceras({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ contacto, nota }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `error ${r.status}`)
      }
      return { ok: true }
    } catch (e) {
      return { ok: false, texto: `No se pudo guardar el contacto: ${e.message}` }
    }
  }

  function cargarConversaciones() {
    if (!token) return
    fetch('/api/conversaciones', { headers: cabeceras() })
      .then((r) => (r.ok ? r.json() : { conversaciones: [] }))
      .then((d) => setConversaciones(d.conversaciones || []))
      .catch(() => {})
  }

  /** Vuelve a abrir una conversación guardada, con sus turnos. */
  async function abrirConversacion(id) {
    if (id === conversacion) return
    try {
      const r = await fetch(`/api/conversacion/${encodeURIComponent(id)}`, {
        headers: cabeceras(),
      })
      if (!r.ok) throw new Error('no se pudo abrir')
      const d = await r.json()
      // Se reconstruye lo que se puede sostener: el texto y quién lo dijo. Las
      // fuentes y las tarjetas no se guardaron, y fabricarlas ahora sería
      // inventarse la evidencia de una respuesta antigua.
      setMensajes(
        (d.turnos || []).map((t) =>
          t.rol === 'cliente'
            ? { rol: 'cliente', texto: t.texto }
            : { rol: 'asistente', respuesta: t.texto, intencion: '', escalado: false,
                fuentes: [], datos: {}, turno: t.turno, historico: true },
        ),
      )
      setConversacion(id)
      setEvidencia(null)
      setLadoAbierto(false)
    } catch {
      setMensajes((m) => [...m, { rol: 'error', texto: 'No se pudo abrir esa conversación.' }])
    }
  }

  /** Empieza un hilo nuevo sin destruir el anterior. */
  function nuevaConsulta() {
    setConversacion('')
    setMensajes([])
    setEvidencia(null)
    setDocumentoActivo(null)
    setLadoAbierto(false)
    entradaRef.current?.focus()
  }

  async function subirDocumento(archivo) {
    if (!archivo) return

    // La espera ocupa un sitio en el hilo desde el primer momento. Indexar un
    // PDF son varios segundos —extraer el texto y embeber cada fragmento— y sin
    // nada en pantalla parece que no ha pasado nada y se vuelve a pulsar.
    const marca = Symbol('carga')
    setMensajes((m) => [
      ...m,
      { rol: 'cargando', marca, nombre: archivo.name, bytes: archivo.size },
    ])

    const resultado = await subir(archivo, conversacion)
    if (archivoRef.current) archivoRef.current.value = ''
    // Es el hilo en el que el documento quedó como asunto en curso. Sin
    // adoptarlo, la pregunta siguiente abriría otra conversación y el documento
    // recién subido no sería el referente de "¿de qué trata?".
    if (resultado?.conversacion) setConversacion(resultado.conversacion)

    // La tarjeta de progreso se sustituye por su desenlace, no se acumula
    // encima: dejar las dos convertiría el hilo en un registro de intentos.
    setMensajes((m) => {
      const sinCarga = m.filter((x) => x.marca !== marca)
      if (!resultado) return sinCarga
      return [
        ...sinCarga,
        resultado.ok
          ? { rol: 'sistema', texto: resultado.texto }
          : { rol: 'error', texto: resultado.texto },
      ]
    })
  }

  async function abrirDocumento(nombre, titulo) {
    const resultado = await descargar(nombre, titulo)
    if (!resultado.ok) {
      setMensajes((m) => [...m, { rol: 'error', texto: resultado.texto }])
    }
  }

  async function eliminarDocumento(nombre, titulo) {
    const resultado = await eliminar(nombre, titulo)
    setMensajes((m) => [
      ...m,
      resultado.ok
        ? { rol: 'sistema', texto: resultado.texto }
        : { rol: 'error', texto: resultado.texto },
    ])
  }

  const correoActual = sesion?.correo
  const modo = salud?.autenticacion
  const pideAcceso = modo === 'google' || modo === 'local'

  function salir() {
    window.google?.accounts?.id?.disableAutoSelect()
    setToken(null)
    setSesion(null)
    setMensajes([])
    setConversacion('')
    setDocumentoActivo(null)
    setEvidencia(null)
  }

  /** Olvida la conversación en el servidor, no solo en pantalla. */
  async function olvidar() {
    if (conversacion) {
      await fetch(`/api/conversacion/${encodeURIComponent(conversacion)}`, {
        method: 'DELETE',
        headers: cabeceras(),
      }).catch(() => {})
    }
    setConversacion('')
    setMensajes([])
    setEvidencia(null)
    cargarConversaciones()
  }

  // Con autenticación real no se dibuja nada hasta que hay sesión válida: una
  // interfaz a medias invita a buscarle la vuelta.
  if (pideAcceso && !(token && sesion?.vinculado)) {
    return (
      <Acceso
        modo={modo}
        clientId={salud.google_client_id}
        onCredencial={setToken}
        onEntrar={entrar}
        ocupado={entrando}
        error={errorAcceso}
      />
    )
  }

  return (
    <div className={`app${ladoAbierto ? ' lado-abierto' : ''}`}>
      {/* Decoración pura: fuera del árbol de accesibilidad y sin eventos. */}
      <div className="lienzo" aria-hidden="true" />

      <a className="saltar" href="#redactor">
        Saltar a la consulta
      </a>

      <header className="barra">
        <div className="marca">
          <button
            type="button"
            className="lado-boton"
            aria-expanded={ladoAbierto}
            aria-controls="lado"
            onClick={() => setLadoAbierto((v) => !v)}
          >
            <IconoDocumento />
            <span className="oculto">
              {ladoAbierto ? 'Ocultar los documentos' : 'Ver los documentos'}
            </span>
          </button>

          <span className="marca-glifo">
            <IconoEscudo />
          </span>
          <div>
            <h1>Asistente de pólizas y facturación</h1>
            <p>RAG con permisos por cliente y escalamiento a humano</p>
          </div>
        </div>

        <div className="barra-estado">
          <Tema tema={tema} onAlternar={alternarTema} />

          {salud && !salud.listo && (
            <span className="pastilla p-mal">
              <IconoEstado ok={false} />
              Servicio no disponible
            </span>
          )}

          {sesion && (
            <div className="sesion">
              {sesion.foto && (
                <img className="sesion-foto" src={sesion.foto} alt="" width="30" height="30" />
              )}
              <span className="sesion-datos">
                <span className="sesion-nombre">{sesion.nombre}</span>
                <span className="sesion-correo">{sesion.correo}</span>
              </span>
              <button type="button" className="eliminar" onClick={salir} aria-label="Cerrar sesión">
                <IconoSalir />
              </button>
            </div>
          )}
        </div>
      </header>

      {ladoAbierto && (
        <button
          type="button"
          className="lado-velo"
          aria-label="Cerrar los documentos"
          onClick={() => setLadoAbierto(false)}
        />
      )}

      <aside className="lado" id="lado" aria-label="Contexto de la consulta">
        <Historial
          conversaciones={conversaciones}
          activa={conversacion}
          onAbrir={abrirConversacion}
          onNueva={nuevaConsulta}
        />

        <Documentos
          documentos={documentos}
          disponible={Boolean(salud?.cargas)}
          activo={documentoActivo?.nombre}
          onSeleccionar={(d) => {
            setDocumentoActivo((actual) => (actual?.nombre === d.nombre ? null : d))
            // El panel tapa la conversación: dejarlo abierto escondería el
            // efecto de lo que se acaba de elegir.
            setLadoAbierto(false)
          }}
          onDescargar={abrirDocumento}
          onEliminar={eliminarDocumento}
        />
      </aside>

      <div className={`principal${evidencia ? ' con-evidencia' : ''}`}>
        {/* Región live: sin esto, un lector de pantalla no anuncia las
            respuestas que llegan y la conversación es invisible para quien
            no la ve. */}
        <main
          className="conversacion"
          aria-live="polite"
          aria-busy={cargando}
          aria-label="Conversación con el asistente"
        >
          {mensajes.length === 0 ? (
            <div className="bienvenida">
              <h2>¿En qué puedo ayudarte?</h2>
              <p>
                Respondo dudas sobre pólizas con la documentación que tiene
                autorizada, y consulto el estado de sus facturas contra el
                sistema de registro. Si no encuentro en qué apoyarme se lo digo
                y le pido más datos; nunca me invento una respuesta.
              </p>
              <div className="tarjetas">
                {sugerencias.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="tarjeta"
                    onClick={() => preguntar(s)}
                    disabled={cargando}
                  >
                    <IconoPregunta />
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="hilo">
              {mensajes.map((m, i) => (
                <Message
                  key={i}
                  mensaje={m}
                  onVerEvidencia={setEvidencia}
                  onValorar={valorar}
                  onEnviarContacto={enviarContacto}
                  onElegirDocumento={elegirDocumento}
                  onPedirHumano={pedirHumano}
                />
              ))}
            </div>
          )}

          {cargando && (
            <div className="hilo">
              <div className="mensaje">
                <span className="avatar de-asistente">
                  <IconoEscudo />
                </span>
                <div className="globo ajeno pensando">
                  <span className="punto" />
                  <span className="punto" />
                  <span className="punto" />
                  {etapa || 'Consultando'}…
                </div>
              </div>
            </div>
          )}
          <div ref={finRef} />
        </main>

        <Evidencia fuente={evidencia} onCerrar={() => setEvidencia(null)} />

        <div className="redactor" id="redactor">
          {/* Las sugerencias desaparecían con la primera pregunta y ya no había
              forma de saber qué más se podía preguntar. Siguen aquí, plegadas,
              porque en el hilo estorbarían. */}
          {verSugerencias && sugerencias.length > 0 && (
            <div className="tarjetas plegadas">
              {sugerencias.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="tarjeta"
                  onClick={() => preguntar(s)}
                  disabled={cargando}
                >
                  <IconoPregunta />
                  {s}
                </button>
              ))}
            </div>
          )}

          {(documentoActivo || mensajes.length > 0) && (
            <div className="contexto">
              {documentoActivo && (
                <span className="contexto-doc">
                  <IconoDocumento />
                  <span className="contexto-nombre">{documentoActivo.titulo}</span>
                  <button
                    type="button"
                    className="contexto-quitar"
                    onClick={() => setDocumentoActivo(null)}
                    aria-label={`Dejar de consultar ${documentoActivo.titulo}`}
                  >
                    <IconoCerrar />
                  </button>
                </span>
              )}
              {mensajes.length > 0 && sugerencias.length > 0 && (
                <button
                  type="button"
                  className="contexto-olvidar"
                  aria-expanded={verSugerencias}
                  onClick={() => setVerSugerencias((v) => !v)}
                >
                  {verSugerencias ? 'Ocultar sugerencias' : 'Qué más puedo preguntar'}
                </button>
              )}
              {mensajes.length > 0 && (
                <button type="button" className="contexto-olvidar" onClick={olvidar}>
                  Olvidar conversación
                </button>
              )}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault()
              preguntar()
            }}
          >
            {salud?.cargas && (
              <label
                className={`adjuntar${subiendo ? ' ocupado' : ''}`}
                title={subiendo ? 'Indexando el documento…' : 'Subir un documento (PDF, .md o .txt)'}
              >
                {subiendo ? <span className="giro" aria-hidden="true" /> : <IconoAdjuntar />}
                <span className="oculto">
                  {subiendo ? 'Subiendo documento' : 'Subir un documento para consultarlo'}
                </span>
                <input
                  ref={archivoRef}
                  type="file"
                  accept={EXTENSIONES}
                  className="oculto"
                  disabled={subiendo || cargando}
                  onChange={(e) => subirDocumento(e.target.files?.[0])}
                />
              </label>
            )}

            <label className="oculto" htmlFor="pregunta">
              Tu consulta{correoActual ? `, como ${correoActual}` : ''}
            </label>
            <input
              id="pregunta"
              ref={entradaRef}
              value={pregunta}
              onChange={(e) => setPregunta(e.target.value)}
              placeholder={subiendo ? 'Indexando el documento…' : 'Escribe tu consulta…'}
              disabled={cargando}
              autoComplete="off"
            />
            <button type="submit" className="enviar" disabled={cargando || !pregunta.trim()}>
              <IconoEnviar />
              <span className="texto-boton">Enviar</span>
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
