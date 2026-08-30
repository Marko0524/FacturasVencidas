import { IconoCerrar, IconoDocumento } from './icons.jsx'

/**
 * El panel de evidencia.
 *
 * La tesis de este asistente es que toda respuesta está anclada en un
 * documento concreto. Mientras eso vive en una nota al pie bajo la burbuja, es
 * una promesa; aquí se puede leer el fragmento entero, con su similitud y su
 * alcance, y comprobarla.
 *
 * Es también lo que llena el vacío de la derecha con algo que importa en lugar
 * de con aire.
 */
export default function Evidencia({ fuente, onCerrar }) {
  if (!fuente) return null

  return (
    <aside className="evidencia" aria-label="Fragmento citado">
      <header className="evidencia-cabecera">
        <span className="bloque-titulo">Evidencia</span>
        <button type="button" className="eliminar" onClick={onCerrar} aria-label="Cerrar evidencia">
          <IconoCerrar />
        </button>
      </header>

      <div className="evidencia-ficha">
        <IconoDocumento />
        <span className="evidencia-titulo">{fuente.titulo}</span>
      </div>

      <dl className="evidencia-datos">
        <div>
          <dt>Fragmento</dt>
          <dd className="mono">{fuente.id}</dd>
        </div>
        {fuente.similitud != null && (
          <div>
            <dt>Similitud</dt>
            <dd className="cifra">{fuente.similitud}</dd>
          </div>
        )}
        <div>
          <dt>Alcance</dt>
          <dd>{fuente.propio ? 'Solo su cuenta' : 'Público'}</dd>
        </div>
      </dl>

      {/* El texto tal cual se recuperó, sin resumir: resumirlo sería volver a
          pedir confianza en vez de darla. */}
      <div className="evidencia-texto">{fuente.texto}</div>

      <p className="pista">
        Esto es literalmente lo que se le pasó al modelo. La respuesta cita este
        identificador y se rechaza si cita alguno que no se haya recuperado.
      </p>
    </aside>
  )
}
