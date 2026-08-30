import { useCallback, useEffect, useState } from 'react'

/**
 * Los documentos del cliente autenticado.
 *
 * Vive en un hook porque ahora hay dos vistas sobre el mismo estado: el botón
 * de subir, que está en el redactor junto a la conversación, y la lista de la
 * barra lateral. Duplicar el estado haría que subir un archivo dejara la lista
 * mostrando lo de antes.
 */
export function useDocumentos({ token, disponible }) {
  const [documentos, setDocumentos] = useState([])
  const [subiendo, setSubiendo] = useState(false)

  // Una sola función arma la identidad, para que ninguna llamada se quede sin
  // ella por descuido.
  const cabeceras = useCallback(
    (extra = {}) =>
      token
        ? { ...extra, Authorization: `Bearer ${token}` }
        : { ...extra, 'X-Demo-Customer': 'logistica' },
    [token],
  )

  const recargar = useCallback(async () => {
    // Hace falta que el servidor acepte cargas Y que haya sesión. `disponible`
    // se ponía a cierto en cuanto respondía /api/salud, así que la lista se
    // pedía antes de entrar y el servidor devolvía un 401 garantizado: una
    // petición que solo podía fallar, y un error en la consola en cada arranque.
    if (!disponible || !token) return
    try {
      const r = await fetch('/api/documentos', { headers: cabeceras() })
      if (!r.ok) throw new Error(`error ${r.status}`)
      const d = await r.json()
      setDocumentos(d.documentos)
    } catch {
      setDocumentos([])
    }
  }, [cabeceras, disponible, token])

  // La lista pertenece a una identidad: al cambiar de cliente se vuelve a
  // pedir, nunca se reutiliza la anterior.
  useEffect(() => {
    recargar()
  }, [recargar])

  /** Sube un archivo. Devuelve `{ok, texto}` para que lo cuente quien llame. */
  const subir = useCallback(
    async (archivo, conversacion = '') => {
      if (!archivo) return null
      setSubiendo(true)

      const cuerpo = new FormData()
      cuerpo.append('archivo', archivo)
      // Con qué conversación viene. El servidor lo usa para que el documento
      // recién subido pase a ser del que se habla: sin esto, subir un archivo y
      // preguntar "¿de qué trata?" resumía el documento anterior.
      if (conversacion) cuerpo.append('conversacion', conversacion)

      try {
        const r = await fetch('/api/documentos', {
          method: 'POST',
          headers: cabeceras(),
          body: cuerpo,
        })
        const d = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(d.detail || `error ${r.status}`)
        await recargar()
        const plural = d.fragmentos === 1 ? 'fragmento' : 'fragmentos'
        return {
          ok: true,
          // El servidor puede haber abierto una conversación para atarle el
          // documento; quien llama la adopta para que la siguiente pregunta
          // caiga en ese mismo hilo.
          conversacion: d.conversacion || '',
          texto: `“${d.titulo}” quedó indexado en ${d.fragmentos} ${plural}. Ya puede preguntarme sobre su contenido.`,
        }
      } catch (error) {
        return { ok: false, texto: `No se pudo subir el documento: ${error.message}` }
      } finally {
        setSubiendo(false)
      }
    },
    [cabeceras, recargar],
  )

  /**
   * Abre el archivo original en una pestaña nueva.
   *
   * Va por `fetch` y no por un `<a href>` porque la petición necesita la
   * cabecera de sesión: un enlace normal la mandaría sin identidad y el
   * servidor respondería 401. Con el blob en la mano, el navegador abre el PDF
   * en su propio visor y quien quiera guardarlo lo hace desde ahí.
   */
  const descargar = useCallback(
    async (nombre, titulo) => {
      try {
        const r = await fetch(`/api/documentos/${encodeURIComponent(nombre)}/archivo`, {
          headers: cabeceras(),
        })
        if (!r.ok) {
          const d = await r.json().catch(() => ({}))
          throw new Error(d.detail || `error ${r.status}`)
        }
        const blob = await r.blob()
        const url = URL.createObjectURL(blob)
        const ventana = window.open(url, '_blank', 'noopener')

        if (!ventana) {
          // Bloqueador de ventanas emergentes: se cae a la descarga, que no
          // depende de que el navegador permita abrir una pestaña.
          const enlace = document.createElement('a')
          enlace.href = url
          enlace.download = nombre.split('/').pop() || 'documento.pdf'
          document.body.appendChild(enlace)
          enlace.click()
          enlace.remove()
        }

        // Revocar de inmediato cancelaría la carga que acaba de empezar; un
        // minuto basta para que el visor lo lea y no deja el archivo retenido
        // en memoria hasta cerrar la pestaña.
        setTimeout(() => URL.revokeObjectURL(url), 60_000)
        return { ok: true, texto: `“${titulo}” abierto.` }
      } catch (error) {
        return { ok: false, texto: `No se pudo abrir el documento: ${error.message}` }
      }
    },
    [cabeceras],
  )

  const eliminar = useCallback(
    async (nombre, titulo) => {
      try {
        const r = await fetch(`/api/documentos/${encodeURIComponent(nombre)}`, {
          method: 'DELETE',
          headers: cabeceras(),
        })
        if (!r.ok) {
          const d = await r.json().catch(() => ({}))
          throw new Error(d.detail || `error ${r.status}`)
        }
        await recargar()
        return { ok: true, texto: `“${titulo}” se eliminó y ya no se consulta.` }
      } catch (error) {
        return { ok: false, texto: `No se pudo eliminar: ${error.message}` }
      }
    },
    [cabeceras, recargar],
  )

  return { documentos, subiendo, subir, descargar, eliminar, recargar }
}
