// Comprobación del análisis con el texto real que devolvió el modelo.
import { readFileSync } from 'node:fs'

const fuente = readFileSync(new URL('../Prosa.jsx', import.meta.url), 'utf8')
// Se extraen las funciones puras: no hace falta React para probar el análisis.
const cuerpo = fuente
  .slice(fuente.indexOf('const VINETA'))
  .replace(/export default function Prosa[\s\S]*?\n}\n/, '')
  .replace(/return \(\s*<strong[\s\S]*?\)\n/, 'return trozo\n')
const modulo = await import(
  'data:text/javascript;base64,' +
  Buffer.from(cuerpo + '\nexport { analizar }').toString('base64')
)

const TEXTO = `Resumen de la Carátula de Póliza – Grupo Meridiano:

*   **Contratante:**
    *   Razón social: Grupo Meridiano, S.A.P.I. de C.V.
    *   RFC: GME180922K41.
    *   Contacto de cobranza: finanzas@meridiano.mx.
*   **Datos de la Póliza:**
    *   Número de póliza: POL-RCG-2026-01193.
    *   Prima anual: $118,400.00 MXN.
*   **Empresas Amparadas:**
    *   La póliza ampara a las cuatro sociedades del grupo listadas en el endoso A-01.
    *   El alta de una sociedad nueva requiere endoso expreso y no opera de forma automática.`

const bloques = modulo.analizar(TEXTO)
let fallos = 0
const ok = (n, c, d = '') => {
  console.log(`  [${c ? 'OK' : 'FALLA'}] ${n}${d ? '  ' + d : ''}`)
  if (!c) fallos++
}

ok('la primera línea es un párrafo', bloques[0].tipo === 'parrafo', bloques[0].texto)
const secciones = bloques.filter((b) => b.tipo === 'seccion')
ok('encuentra las tres secciones', secciones.length === 3,
   secciones.map((s) => s.titulo).join(' | '))
ok('los títulos van sin asteriscos', secciones.every((s) => !s.titulo.includes('*')))
ok('Contratante son 3 pares', secciones[0].pares.length === 3,
   JSON.stringify(secciones[0].pares[0]))
ok('la clave no arrastra el valor', secciones[0].pares[0].clave === 'Razón social')
ok('el valor no arrastra la clave', secciones[0].pares[0].valor === 'Grupo Meridiano, S.A.P.I. de C.V.')
ok('las frases largas NO se parten en pares', secciones[2].pares.length === 0 &&
   secciones[2].sueltos.length === 2, `pares=${secciones[2].pares.length} sueltos=${secciones[2].sueltos.length}`)

// Una respuesta normal de dos frases debe seguir siendo un párrafo.
const simple = modulo.analizar('El deducible es del 2% sobre el valor facturado.')
ok('una respuesta breve sigue siendo párrafo',
   simple.length === 1 && simple[0].tipo === 'parrafo')

console.log(fallos ? `\nFALLOS: ${fallos}` : '\nTODO CORRECTO')
process.exit(fallos ? 1 : 0)
