/**
 * Análisis de la respuesta del asistente en bloques.
 *
 * Separado del componente a propósito: es lógica pura —texto entra, estructura
 * sale— y así se prueba sin montar React ni un navegador.
 *
 * Se construye primero un árbol por sangría y luego se decide cómo se dibuja
 * cada rama. La primera versión asumía dos niveles y el modelo devolvió tres
 * ("Coberturas" > "Responsabilidad civil general" > "Suma asegurada: …"): los
 * subtítulos caían en una lista y sus cifras en otra, así que en pantalla la
 * suma asegurada aparecía separada de la cobertura a la que pertenecía. Un
 * formato que despareja un dato de su etiqueta es peor que uno feo.
 */

// Viñeta de Markdown o de las que escribe el modelo, con su sangría.
const VINETA = /^(\s*)(?:[-*•]|\d+[.)])\s+(.*)$/

// "Clave: valor", con la clave corta. El límite de longitud es lo que separa un
// par de datos de una frase que casualmente lleva dos puntos en medio.
const PAR = /^\s*(?:\*\*)?([^:*]{2,42}?)(?:\*\*)?\s*:\s+(.+)$/

// Una viñeta que solo enuncia un tema, sin valor detrás de los dos puntos.
const TITULO = /^(?:\*\*(.+?)\*\*\s*:?\s*|([^:*]{2,42}):)$/

/** Convierte el texto en bloques listos para dibujar. */
export function analizar(texto) {
  const bloques = []
  let lista = null

  for (const nodo of arbol(String(texto || ''))) {
    if (nodo.suelto) {
      bloques.push({ tipo: 'parrafo', texto: nodo.contenido })
      lista = null
      continue
    }

    const titulo = nodo.contenido.match(TITULO)
    if (titulo && nodo.hijos.length) {
      bloques.push({
        tipo: 'seccion',
        titulo: limpiar(titulo[1] || titulo[2]).replace(/:$/, ''),
        ...repartir(nodo.hijos),
      })
      lista = null
      continue
    }

    // Una viñeta sin hijos y sin forma de título es un punto de lista; las
    // consecutivas se agrupan en una sola.
    if (!lista) {
      lista = { tipo: 'lista', puntos: [] }
      bloques.push(lista)
    }
    lista.puntos.push(nodo.contenido)
    for (const hijo of nodo.hijos) lista.puntos.push(hijo.contenido)
  }

  return bloques.length ? bloques : [{ tipo: 'parrafo', texto: String(texto || '') }]
}

/**
 * Reparte los hijos de una sección en pares de datos, subgrupos y frases.
 *
 * El orden se conserva dentro de cada montón, pero los tres se dibujan en
 * bloques separados: mezclar una tabla de "clave: valor" con frases largas
 * rompe la alineación en dos columnas, que es lo único que hace consultable
 * una carátula con veinte datos.
 */
function repartir(hijos) {
  const pares = []
  const grupos = []
  const sueltos = []

  for (const hijo of hijos) {
    if (hijo.hijos.length) {
      const titulo = hijo.contenido.match(TITULO)
      grupos.push({
        titulo: limpiar(titulo ? titulo[1] || titulo[2] : hijo.contenido).replace(/:$/, ''),
        ...repartir(hijo.hijos),
      })
      continue
    }
    const par = hijo.contenido.match(PAR)
    if (par) pares.push({ clave: limpiar(par[1]), valor: par[2].trim() })
    else sueltos.push(hijo.contenido)
  }

  return { pares, grupos, sueltos }
}

/** Las líneas como árbol, anidado por sangría. */
function arbol(texto) {
  const raiz = []
  // Cada nivel abierto, con la sangría a la que se abrió.
  const pila = [{ sangria: -1, hijos: raiz }]

  for (const linea of texto.split('\n')) {
    if (!linea.trim()) continue

    const vineta = linea.match(VINETA)
    if (!vineta) {
      // Texto sin viñeta: cierra todo lo abierto y vale por sí mismo.
      pila.length = 1
      raiz.push({ contenido: linea.trim(), hijos: [], suelto: true })
      continue
    }

    const sangria = vineta[1].length
    const nodo = { contenido: vineta[2].trim(), hijos: [], suelto: false }

    while (pila.length > 1 && sangria <= pila[pila.length - 1].sangria) pila.pop()
    pila[pila.length - 1].hijos.push(nodo)
    pila.push({ sangria, hijos: nodo.hijos })
  }

  return raiz
}

function limpiar(texto) {
  return String(texto).replace(/\*\*/g, '').trim()
}
