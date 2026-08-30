/**
 * Lectura de un flujo de eventos del servidor (SSE) sobre una respuesta `fetch`.
 *
 * No se usa `EventSource` porque solo sabe hacer GET, y la pregunta va en el
 * cuerpo de un POST — meterla en la URL la dejaría escrita en los logs de
 * cualquier proxy del camino, junto con el token.
 *
 * El troceado importa: `fetch` entrega los bytes como lleguen, y un evento puede
 * partirse por la mitad entre dos lecturas. Por eso se acumula en `resto` hasta
 * ver la línea en blanco que cierra cada evento, y lo que quede sin cerrar
 * espera a la siguiente vuelta.
 */
export async function leerFlujo(respuesta, { onEtapa }) {
  if (!respuesta.body) {
    throw new Error('este navegador no puede leer la respuesta por partes')
  }

  const lector = respuesta.body.getReader()
  const decodificador = new TextDecoder()
  let resto = ''
  let datos = null

  while (true) {
    const { value, done } = await lector.read()
    if (done) break

    // `stream: true` para no romper un carácter multibyte partido entre dos
    // trozos: sin él, un acento a caballo entre dos lecturas sale como basura.
    resto += decodificador.decode(value, { stream: true })

    const bloques = resto.split('\n\n')
    resto = bloques.pop() ?? ''

    for (const bloque of bloques) {
      const evento = interpretar(bloque)
      if (!evento) continue
      if (evento.nombre === 'etapa') onEtapa?.(evento.datos.etapa)
      else if (evento.nombre === 'respuesta') datos = evento.datos
      else if (evento.nombre === 'error') throw new Error(evento.datos.detalle)
    }
  }

  if (!datos) throw new Error('la respuesta llegó incompleta')
  return datos
}

function interpretar(bloque) {
  let nombre = ''
  const lineas = []
  for (const linea of bloque.split('\n')) {
    if (linea.startsWith('event:')) nombre = linea.slice(6).trim()
    else if (linea.startsWith('data:')) lineas.push(linea.slice(5).trim())
  }
  if (!nombre || lineas.length === 0) return null
  try {
    return { nombre, datos: JSON.parse(lineas.join('\n')) }
  } catch {
    return null
  }
}
