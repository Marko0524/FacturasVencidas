/**
 * Comprobación del análisis con el texto que devolvió el modelo de verdad.
 *
 * Se ejecuta con `node src/prosa.prueba.mjs`. No necesita React ni navegador:
 * el análisis es una función pura, y probarla así cuesta 40 ms.
 */

import { analizar } from './prosa.js'

/** Todo el texto de un bloque, en orden, para comprobar que no se pierde nada. */
function aplanar(bloque) {
  return [
    `${bloque.titulo}:`,
    ...bloque.pares.map((p) => `${p.clave}: ${p.valor}`),
    ...(bloque.grupos || []).flatMap(aplanar),
    ...bloque.sueltos,
  ]
}

let fallos = 0
const ok = (nombre, cond, detalle = '') => {
  console.log(`  [${cond ? 'OK' : 'FALLA'}] ${nombre}${detalle ? '  ' + detalle : ''}`)
  if (!cond) fallos++
}

// Copiado literalmente de una respuesta real de Vertex sobre la carátula.
const CARATULA = `Resumen de la Carátula de Póliza – Grupo Meridiano:

*   **Contratante:**
    *   Razón social: Grupo Meridiano, S.A.P.I. de C.V.
    *   RFC: GME180922K41.
    *   Contacto de cobranza: finanzas@meridiano.mx.
*   **Datos de la Póliza:**
    *   Número de póliza: POL-RCG-2026-01193.
    *   Prima anual: $118,400.00 MXN.
*   **Deducibles:**
    *   Responsabilidad civil general: $15,000.00 fijo.
    *   Gastos de defensa jurídica: Sin deducible.
*   **Empresas Amparadas:**
    *   La póliza ampara a las cuatro sociedades del grupo listadas en el endoso A-01.
    *   El alta de una sociedad nueva requiere endoso expreso y no opera de forma automática.`

console.log('=== carátula de póliza ===')
const bloques = analizar(CARATULA)
const secciones = bloques.filter((b) => b.tipo === 'seccion')

ok('la entradilla es un párrafo', bloques[0].tipo === 'parrafo', bloques[0].texto)
ok('encuentra las cuatro secciones', secciones.length === 4,
  secciones.map((s) => s.titulo).join(' · '))
ok('los títulos van sin asteriscos', secciones.every((s) => !s.titulo.includes('*')))
ok('ni con dos puntos al final', secciones.every((s) => !s.titulo.endsWith(':')))

const contratante = secciones[0]
ok('Contratante trae 3 pares', contratante.pares.length === 3)
ok('la clave no arrastra el valor', contratante.pares[0].clave === 'Razón social',
  contratante.pares[0].clave)
ok('el valor no arrastra la clave',
  contratante.pares[0].valor === 'Grupo Meridiano, S.A.P.I. de C.V.',
  contratante.pares[0].valor)

const deducibles = secciones[2]
ok('un importe con coma y punto sobrevive',
  deducibles.pares[0].valor === '$15,000.00 fijo.', deducibles.pares[0].valor)

// El caso que rompería un análisis ingenuo: frases largas con dos puntos dentro
// no son pares de datos, y partirlas inventaría una clave que nadie escribió.
const amparadas = secciones[3]
ok('las frases largas NO se convierten en pares',
  amparadas.pares.length === 0 && amparadas.sueltos.length === 2,
  `pares=${amparadas.pares.length} sueltos=${amparadas.sueltos.length}`)

console.log('\n=== tres niveles de anidamiento ===')

// El caso que rompía: el modelo agrupa por cobertura y cuelga de cada una sus
// cifras. Con solo dos niveles, "Suma asegurada" se dibujaba lejos de la
// cobertura a la que pertenece —emparejada visualmente con la de al lado—, que
// es un error de contenido y no de estética.
const COBERTURAS = `- **Coberturas y límites:**
  - Responsabilidad civil general:
    - Suma asegurada: $6,000,000.00
    - Deducible: $15,000.00 fijo
  - Gastos de defensa jurídica:
    - Suma asegurada: $750,000.00
    - Deducible: Sin deducible`

const cob = analizar(COBERTURAS)
ok('sigue siendo una sola sección', cob.length === 1 && cob[0].tipo === 'seccion',
  cob[0].titulo)
ok('con dos subgrupos', cob[0].grupos.length === 2,
  cob[0].grupos.map((g) => g.titulo).join(' · '))
ok('cada cifra va con SU cobertura',
  cob[0].grupos[0].pares[0].valor === '$6,000,000.00' &&
  cob[0].grupos[1].pares[0].valor === '$750,000.00',
  cob[0].grupos.map((g) => `${g.titulo}=${g.pares[0].valor}`).join(' | '))
ok('nada queda huérfano en el nivel de arriba',
  cob[0].pares.length === 0 && cob[0].sueltos.length === 0)
ok('tampoco se pierde nada en tres niveles',
  aplanar(cob[0]).join(' ').replace(/\s+/g, ' ') ===
    COBERTURAS.replace(/[-*\s]+/g, ' ').replace(/\*\*/g, '').trim(),
  aplanar(cob[0]).join(' '))

console.log('\n=== respuestas normales ===')
const breve = analizar('El deducible es del 2% sobre el valor facturado.')
ok('una respuesta de una frase sigue siendo un párrafo',
  breve.length === 1 && breve[0].tipo === 'parrafo')

const lista = analizar('- Primero\n- Segundo\n- Tercero')
ok('una lista suelta se agrupa en una sola',
  lista.length === 1 && lista[0].tipo === 'lista' && lista[0].puntos.length === 3)

const guiones = analizar('* **Cobertura:**\n  - Daños: $1,000.00')
ok('sirve igual con guion que con asterisco',
  guiones[0].tipo === 'seccion' && guiones[0].pares[0].clave === 'Daños')

console.log('\n=== nada se pierde por el camino ===')
const vacio = analizar('')
ok('el texto vacío no revienta', vacio.length === 1 && vacio[0].texto === '')
ok('sin viñetas devuelve el texto íntegro',
  analizar('Una frase.\nOtra frase.').map((b) => b.texto).join(' ') === 'Una frase. Otra frase.')

// Lo importante de todo esto: se cambia la presentación, no el contenido.
const original = CARATULA.replace(/[*\s]+/g, ' ').trim()
const pintado = bloques
  .flatMap((b) =>
    b.tipo === 'parrafo' ? [b.texto]
      : b.tipo === 'lista' ? b.puntos
      : aplanar(b))
  .join(' ')
  .replace(/[*\s]+/g, ' ')
  .trim()
ok('no se pierde ni una palabra del resumen', original === pintado,
  original === pintado ? '' : `\n    esperado: ${original.slice(0, 120)}\n    obtenido: ${pintado.slice(0, 120)}`)

console.log(fallos ? `\nFALLOS: ${fallos}` : '\nTODO CORRECTO')
process.exit(fallos ? 1 : 0)
