import { TarjetaCuenta, TarjetaFactura } from './Factura.jsx'
import Prosa from './Prosa.jsx'
import Traspaso from './Traspaso.jsx'
import Valoracion from './Valoracion.jsx'
import {
  IconoCheck,
  IconoTraspaso,
  IconoDocumento,
  IconoEscalado,
  IconoEscudo,
  IconoFactura,
  IconoHumano,
  IconoPoliza,
  IconoUsuario,
} from './icons.jsx'

const INTENCION = {
  POLIZA: { etiqueta: 'Póliza', Icono: IconoPoliza, clase: 'p-poliza' },
  RESUMEN: { etiqueta: 'Resumen', Icono: IconoDocumento, clase: 'p-poliza' },
  CAPACIDADES: { etiqueta: 'Qué puedo hacer', Icono: IconoEscudo, clase: 'p-poliza' },
  CONTEXTO: { etiqueta: 'Sesión', Icono: IconoEscudo, clase: 'p-poliza' },
  FACTURA: { etiqueta: 'Factura', Icono: IconoFactura, clase: 'p-factura' },
  HUMANO: { etiqueta: 'Humano', Icono: IconoHumano, clase: 'p-humano' },
}

export default function Message({
  mensaje,
  onVerEvidencia,
  onValorar,
  onEnviarContacto,
  onElegirDocumento,
  onPedirHumano,
}) {
  if (mensaje.rol === 'cliente') {
    return (
      <div className="mensaje propio">
        <span className="avatar de-cliente">
          <IconoUsuario />
        </span>
        <div className="globo propio">
          <span className="oculto">Usted preguntó: </span>
          {mensaje.texto}
        </div>
      </div>
    )
  }

  // La carga de un documento en curso. Es indeterminada a propósito: el
  // servidor no informa de avance, y una barra que fingiera porcentajes estaría
  // inventando. Lo que sí se puede decir con verdad es qué archivo es, cuánto
  // pesa y en qué consiste la espera.
  if (mensaje.rol === 'cargando') {
    return (
      <article className="cargando" aria-live="polite" aria-busy="true">
        <div className="cargando-cabecera">
          <IconoDocumento />
          <span className="cargando-nombre">{mensaje.nombre}</span>
          <span className="cargando-peso">{formatoPeso(mensaje.bytes)}</span>
        </div>

        <div
          className="progreso"
          role="progressbar"
          aria-label={`Indexando ${mensaje.nombre}`}
        >
          <span className="progreso-avance" />
        </div>

        <p className="cargando-pie">
          Extrayendo el texto e indexando los fragmentos. Puede tardar unos segundos.
        </p>
      </article>
    )
  }

  // Un aviso del sistema —una carga que terminó, un borrado— no es una
  // respuesta del asistente: no lleva avatar ni fuentes, y se lee como una nota
  // en el margen del hilo.
  if (mensaje.rol === 'sistema') {
    return (
      <p className="nota-sistema">
        <IconoCheck />
        {mensaje.texto}
      </p>
    )
  }

  if (mensaje.rol === 'error') {
    // role="alert" para que un lector de pantalla lo anuncie de inmediato:
    // un fallo de red no puede esperar a que alguien lo descubra leyendo.
    return (
      <div className="mensaje">
        <span className="avatar de-alerta">
          <IconoEscalado />
        </span>
        <div className="globo fallo" role="alert">
          {mensaje.texto}
        </div>
      </div>
    )
  }

  const { respuesta, intencion, escalado, fuentes = [], motivo, datos, folio } = mensaje

  if (escalado) {
    return <Traspaso mensaje={mensaje} onEnviarContacto={onEnviarContacto} />
  }

  const { etiqueta, Icono, clase } = INTENCION[intencion] || {
    etiqueta: intencion,
    Icono: IconoHumano,
    clase: 'p-humano',
  }

  return (
    <div className="mensaje">
      <span className={`avatar ${escalado ? 'de-alerta' : 'de-asistente'}`}>
        {escalado ? <IconoEscalado /> : <IconoEscudo />}
      </span>

      <div className={`globo ajeno${escalado ? ' escalado' : ''}`}>
        <div className="etiquetas">
          <span className={`pastilla ${clase}`}>
            <Icono />
            {etiqueta}
          </span>
          {escalado && (
            <span className="pastilla p-escalado">
              <IconoEscalado />
              Escalado a humano
            </span>
          )}
        </div>

        {datos?.tipo === 'capacidades' && (
          <div className="capacidades">
            <p className="capacidades-intro">
              Soy el asistente de pólizas y facturación.
            </p>
            <p className="capacidades-titulo">Puedo</p>
            <ul className="capacidades-lista puede">
              {datos.puedo.map((linea) => (
                <li key={linea}>
                  <IconoCheck />
                  {linea}
                </li>
              ))}
            </ul>
            <p className="capacidades-titulo">No puedo, lo paso a una persona</p>
            <ul className="capacidades-lista no-puede">
              {datos.no_puedo.map((linea) => (
                <li key={linea}>
                  <IconoTraspaso />
                  {linea}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Falta saber de qué documento se habla. Antes esto era un
            escalamiento a una persona: un traspaso gastado en un dato que
            quien pregunta puede dar en un toque. */}
        {datos?.tipo === 'elegir_documento' && (
          <div className="elegir">
            <p className="elegir-texto">{respuesta}</p>
            <div className="elegir-opciones">
              {datos.opciones.map((o) => (
                <button
                  key={o.nombre}
                  type="button"
                  className="elegir-opcion"
                  onClick={() => onElegirDocumento?.(o)}
                >
                  <IconoDocumento />
                  {o.titulo}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* No encontró evidencia. Antes esto traspasaba a una persona sin
            preguntar; ahora pide el dato que falta y deja la salida humana
            como opción, no como consecuencia. */}
        {datos?.tipo === 'aclarar' && (
          <div className="aclarar">
            <Prosa texto={respuesta} className="cuerpo prosa" />

            {datos.documentos?.length > 0 && (
              <>
                <p className="aclarar-titulo">Puedo mirar en</p>
                <ul className="aclarar-lista">
                  {datos.documentos.map((d) => (
                    <li key={d}>
                      <IconoDocumento />
                      {d}
                    </li>
                  ))}
                </ul>
              </>
            )}

            {datos.ofrecer_humano && (
              <button type="button" className="aclarar-humano" onClick={onPedirHumano}>
                <IconoTraspaso />
                Prefiero que lo vea una persona
              </button>
            )}
          </div>
        )}

        {datos?.tipo === 'resumen' && (
          <p className="resumen-aviso">
            <IconoDocumento />
            <span>
              Resumen de <strong>{datos.titulo}</strong> ({datos.secciones}{' '}
              {datos.secciones === 1 ? 'sección' : 'secciones'}). No sustituye al
              documento: puede abrirlo desde la barra lateral.
            </span>
          </p>
        )}

        {datos?.tipo === 'factura' && <TarjetaFactura datos={datos} />}
        {datos?.tipo === 'cuenta' && datos.pendientes > 0 && <TarjetaCuenta datos={datos} />}

        {/* Con tarjeta, la prosa se queda con la parte que la tarjeta no dice:
            la frase que explica qué hacer. Repetir las cifras en dos formatos
            es ruido, y una de las dos acabaría desactualizada. */}
        {/* Con tarjeta, la prosa se queda con lo que la tarjeta no dice: qué
            hacer. Repetir las cifras en dos formatos es ruido, y una de las dos
            copias acabaría desactualizada. La nota la redacta el backend. */}
        {datos?.tipo === 'factura' || datos?.tipo === 'cuenta' ? (
          datos.nota && <p className="cuerpo prosa resumen">{datos.nota}</p>
        ) : datos?.tipo === 'elegir_documento' || datos?.tipo === 'aclarar' ? null : datos?.tipo === 'capacidades' ? (
          <p className="cuerpo prosa resumen">{(datos.cierre || []).join(' ')}</p>
        ) : (
          <Prosa texto={respuesta} />
        )}

        {escalado && motivo && (
          <p className="motivo">
            <strong>Motivo:</strong> {motivo}
          </p>
        )}

        {fuentes.length > 0 && (
          <div className="fuentes">
            <span className="fuentes-titulo" id={`fuentes-${fuentes[0].id}`}>
              {fuentes.length === 1 ? 'Fuente' : `${fuentes.length} fuentes`}
            </span>
            {/* Agrupadas por documento. Un resumen cita seis fragmentos de la
                misma carátula, y listarlos sueltos repetía seis veces el mismo
                título: mucho ruido para decir "salió de un solo documento". El
                fragmento sigue siendo la unidad al abrir la evidencia, que es
                donde importa saber exactamente de dónde vino la frase. */}
            <ul aria-labelledby={`fuentes-${fuentes[0].id}`}>
              {agrupar(fuentes).map((grupo) => (
                <li key={grupo.clave}>
                  {grupo.partes[0].texto ? (
                    <div className="fuente-grupo">
                      <span className="fuente-cabecera">
                        <IconoDocumento />
                        <span className="fuente-nombre">{grupo.titulo}</span>
                        {grupo.partes.length > 1 && (
                          <span className="fuente-cuenta">
                            {grupo.partes.length} fragmentos
                          </span>
                        )}
                      </span>
                      <span className="fuente-partes">
                        {grupo.partes.map((f) => (
                          <button
                            key={f.id}
                            type="button"
                            className="fuente-parte"
                            onClick={() => onVerEvidencia(f)}
                            title={`Ver el fragmento ${f.id}`}
                          >
                            <span className="mono">{etiquetaFragmento(f.id)}</span>
                            {f.similitud != null && (
                              <span className="medida">{f.similitud}</span>
                            )}
                          </button>
                        ))}
                      </span>
                    </div>
                  ) : (
                    <span className="fuente-estatica">
                      <IconoFactura />
                      <span className="fuente-nombre">{grupo.titulo}</span>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <Valoracion turno={mensaje.turno} onValorar={onValorar} />
      </div>
    </div>
  )
}


/**
 * Las fuentes por documento, conservando el orden en que las citó la respuesta.
 *
 * Se agrupa por `documento` cuando lo hay y por título cuando no: una fuente de
 * factura no tiene documento, y agruparlas todas bajo la clave vacía juntaría
 * facturas distintas en una sola fila.
 */
function agrupar(fuentes) {
  const grupos = new Map()
  for (const f of fuentes) {
    const clave = f.documento || f.titulo || f.id
    if (!grupos.has(clave)) grupos.set(clave, { clave, titulo: f.titulo, partes: [] })
    grupos.get(clave).partes.push(f)
  }
  return [...grupos.values()]
}

/** De "caratula-grupo-meridiano#4" queda "#4": el título ya está encima. */
function etiquetaFragmento(id) {
  const corte = String(id).lastIndexOf('#')
  return corte === -1 ? id : id.slice(corte)
}

/** Peso del archivo en unidades que una persona lee de un vistazo. */
function formatoPeso(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
