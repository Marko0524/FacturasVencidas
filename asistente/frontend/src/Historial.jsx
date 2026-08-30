import { IconoHistorial, IconoNueva } from './icons.jsx'

/**
 * Las conversaciones anteriores de esta cuenta.
 *
 * Antes solo existía un hilo y la única forma de gestionarlo era "Olvidar
 * conversación", que lo borraba. Consultar algo nuevo obligaba a destruir lo
 * anterior — y como la memoria de servidor ya guardaba los turnos, lo que se
 * borraba existía y era recuperable; simplemente no había por dónde volver.
 *
 * El título es la primera pregunta, congelada. Un nombre que se recalcula solo
 * no sirve para reconocer nada en una lista.
 */
export default function Historial({ conversaciones, activa, onAbrir, onNueva }) {
  return (
    <section>
      <span className="bloque-titulo">
        <IconoHistorial />
        Conversaciones
      </span>

      <button type="button" className="historial-nueva" onClick={onNueva}>
        <IconoNueva />
        Nueva consulta
      </button>

      {conversaciones.length === 0 ? (
        <p className="lado-vacio">Aún no hay conversaciones guardadas.</p>
      ) : (
        <ul className="historial">
          {conversaciones.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className={`historial-item${c.id === activa ? ' activa' : ''}`}
                onClick={() => onAbrir(c.id)}
                aria-current={c.id === activa ? 'true' : undefined}
              >
                <span className="historial-titulo">{c.titulo}</span>
                <span className="historial-meta">
                  {c.preguntas} {c.preguntas === 1 ? 'pregunta' : 'preguntas'} ·{' '}
                  {fecha(c.ultima_en)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** Relativa mientras es reciente, que es cuando "hace 5 min" dice más que la hora. */
function fecha(iso) {
  const cuando = new Date(iso)
  if (Number.isNaN(cuando.getTime())) return ''
  const minutos = Math.round((Date.now() - cuando.getTime()) / 60000)
  if (minutos < 1) return 'ahora'
  if (minutos < 60) return `hace ${minutos} min`
  if (minutos < 60 * 24) return `hace ${Math.round(minutos / 60)} h`
  return cuando.toLocaleDateString('es-MX', { day: 'numeric', month: 'short' })
}
