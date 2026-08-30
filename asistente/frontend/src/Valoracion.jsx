import { useState } from 'react'
import { IconoNoUtil, IconoUtil } from './icons.jsx'

/**
 * Si la respuesta sirvió.
 *
 * Todo el asistente está construido alrededor de callarse cuando no tiene
 * evidencia. Faltaba la señal contraria: cuando sí respondió y se equivocó. Ese
 * caso no deja rastro en ningún log —la respuesta se generó con normalidad— y
 * es el único dato que dice si el umbral de similitud está bien puesto.
 *
 * Un "no me sirvió" abre un campo de texto porque el pulgar solo dice que algo
 * falló, no qué. Sin el porqué, el dato dice que revises algo pero no qué
 * revisar. El pulgar arriba no lo abre: cuando algo sale bien, nadie escribe.
 */
export default function Valoracion({ turno, onValorar }) {
  const [dado, setDado] = useState(null)
  const [comentando, setComentando] = useState(false)
  const [comentario, setComentario] = useState('')
  const [gracias, setGracias] = useState(false)

  if (!turno) return null

  async function valorar(util) {
    setDado(util)
    setGracias(util)
    setComentando(!util)
    await onValorar(turno, util, '')
  }

  async function enviarComentario(e) {
    e.preventDefault()
    await onValorar(turno, false, comentario.trim())
    setComentando(false)
    setGracias(true)
  }

  if (gracias && !comentando) {
    return <p className="valoracion-gracias">Gracias, queda anotado.</p>
  }

  return (
    <div className="valoracion">
      {!comentando && (
        <>
          <span className="valoracion-texto">¿Le sirvió?</span>
          <button
            type="button"
            className={`valoracion-boton${dado === true ? ' elegido' : ''}`}
            onClick={() => valorar(true)}
            aria-pressed={dado === true}
          >
            <IconoUtil />
            <span className="oculto">Sí, me sirvió</span>
          </button>
          <button
            type="button"
            className={`valoracion-boton${dado === false ? ' elegido' : ''}`}
            onClick={() => valorar(false)}
            aria-pressed={dado === false}
          >
            <IconoNoUtil />
            <span className="oculto">No me sirvió</span>
          </button>
        </>
      )}

      {comentando && (
        <form className="valoracion-comentario" onSubmit={enviarComentario}>
          <label htmlFor={`comentario-${turno}`}>¿Qué esperaba encontrar?</label>
          <div className="valoracion-campos">
            <input
              id={`comentario-${turno}`}
              value={comentario}
              onChange={(e) => setComentario(e.target.value)}
              placeholder="Opcional, pero es lo que hace útil el aviso"
              autoComplete="off"
              maxLength={1000}
            />
            <button type="submit">Enviar</button>
          </div>
        </form>
      )}
    </div>
  )
}
