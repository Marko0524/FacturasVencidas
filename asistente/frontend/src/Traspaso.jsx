import { useState } from 'react'
import { IconoCheck, IconoTraspaso } from './icons.jsx'

/**
 * Un escalamiento, con algo que hacer a continuación.
 *
 * Antes esto era un callejón: folio, motivo y punto. El texto prometía que un
 * ejecutivo contactaría "por este mismo medio" cuando no había medio, ni cola,
 * ni registro — el folio se escribía en el log del servidor y se tiraba. Ahora
 * el caso existe de verdad y lo único que faltaba es lo que solo la persona
 * sabe: por dónde localizarla.
 *
 * El formulario no se envía solo ni pide nada obligatorio. Quien no quiera dejar
 * un teléfono se queda igual que antes, con su folio.
 */
export default function Traspaso({ mensaje, onEnviarContacto }) {
  const { respuesta, motivo, datos, folio } = mensaje
  const [contacto, setContacto] = useState('')
  const [nota, setNota] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [enviado, setEnviado] = useState(false)
  const [error, setError] = useState('')

  async function enviar(e) {
    e.preventDefault()
    if (!contacto.trim() || enviando) return
    setEnviando(true)
    setError('')
    const resultado = await onEnviarContacto(folio, contacto.trim(), nota.trim())
    setEnviando(false)
    if (resultado.ok) setEnviado(true)
    else setError(resultado.texto)
  }

  return (
    <div className="mensaje">
      <span className="avatar de-alerta">
        <IconoTraspaso />
      </span>
      <article className="traspaso">
        <header className="traspaso-cabecera">
          <span className="traspaso-titulo">Escalado a una persona</span>
          {folio && <span className="mono traspaso-folio">{folio}</span>}
        </header>

        <p className="traspaso-texto">{respuesta}</p>

        <dl className="traspaso-datos">
          {datos?.destino && (
            <div>
              <dt>Destino</dt>
              <dd>{datos.destino}</dd>
            </div>
          )}
          {motivo && (
            <div>
              <dt>Motivo</dt>
              <dd>{motivo}</dd>
            </div>
          )}
        </dl>

        {enviado ? (
          <p className="traspaso-hecho">
            <IconoCheck />
            Contacto añadido al caso {folio}.
          </p>
        ) : (
          folio && (
            <form className="traspaso-contacto" onSubmit={enviar}>
              <label htmlFor={`contacto-${folio}`}>¿Cómo prefiere que le contacten?</label>
              <div className="traspaso-campos">
                <input
                  id={`contacto-${folio}`}
                  value={contacto}
                  onChange={(e) => setContacto(e.target.value)}
                  placeholder="Teléfono, correo u horario"
                  disabled={enviando}
                  autoComplete="off"
                  maxLength={200}
                />
                <button type="submit" disabled={enviando || !contacto.trim()}>
                  {enviando ? 'Guardando…' : 'Añadir al caso'}
                </button>
              </div>
              <textarea
                value={nota}
                onChange={(e) => setNota(e.target.value)}
                placeholder="Si quiere añadir algo más sobre el caso (opcional)"
                disabled={enviando}
                rows={2}
                maxLength={1000}
              />
              {error && (
                <p className="traspaso-error" role="alert">
                  {error}
                </p>
              )}
            </form>
          )
        )}
      </article>
    </div>
  )
}
