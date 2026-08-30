import { analizar } from './prosa.js'

/**
 * La respuesta del asistente, con la estructura que trae.
 *
 * El modelo devuelve viñetas agrupadas por tema —"**Contratante:**" y debajo
 * "Razón social: …", "RFC: …"— y todo eso se pintaba dentro de un solo <p> con
 * `white-space: pre-wrap`. El resultado era el texto crudo: asteriscos a la
 * vista, y una carátula de póliza con veinte datos leyéndose como un párrafo.
 * La información estaba, pero no se podía consultar de un vistazo, que es justo
 * para lo que existe una carátula.
 *
 * Un grupo de "Clave: valor" se dibuja como tabla de definiciones y no como
 * lista: son pares, y alinearlos en dos columnas es lo que permite buscar
 * "deducible" con la vista sin leerse las otras diez líneas.
 *
 * Se construyen elementos de React, nunca HTML. El texto lo escribe un modelo a
 * partir de documentos que sube cualquiera: es la última cadena del mundo a la
 * que conviene darle `dangerouslySetInnerHTML`.
 */
export default function Prosa({ texto, className = 'cuerpo prosa' }) {
  const bloques = analizar(texto)

  // Sin estructura reconocible se deja como estaba: un párrafo. Es lo correcto
  // para una respuesta de dos frases, que son la mayoría.
  if (bloques.length === 1 && bloques[0].tipo === 'parrafo') {
    return <p className={className}>{bloques[0].texto}</p>
  }

  return (
    <div className={className}>
      {bloques.map((bloque, i) => {
        if (bloque.tipo === 'parrafo') {
          return <p key={i}>{negritas(bloque.texto)}</p>
        }

        if (bloque.tipo === 'seccion') {
          return (
            <section key={i} className="prosa-seccion">
              <h4>{bloque.titulo}</h4>
              <Contenido bloque={bloque} />
            </section>
          )
        }

        return (
          <ul key={i} className="prosa-lista">
            {bloque.puntos.map((linea, j) => (
              <li key={j}>{negritas(linea)}</li>
            ))}
          </ul>
        )
      })}
    </div>
  )
}

/**
 * Los datos de una sección: pares, subgrupos y frases.
 *
 * Se llama a sí misma para el tercer nivel —"Coberturas" > "Responsabilidad
 * civil general" > "Suma asegurada: …"— porque el modelo lo usa y sin esto la
 * cifra se dibujaba lejos de la cobertura a la que pertenece.
 */
function Contenido({ bloque }) {
  return (
    <>
      {bloque.pares.length > 0 && (
        <dl className="prosa-datos">
          {bloque.pares.map((par, j) => (
            <div key={j}>
              <dt>{par.clave}</dt>
              <dd>{negritas(par.valor)}</dd>
            </div>
          ))}
        </dl>
      )}

      {bloque.grupos?.map((grupo, j) => (
        <div key={j} className="prosa-subgrupo">
          <h5>{grupo.titulo}</h5>
          <Contenido bloque={grupo} />
        </div>
      ))}

      {bloque.sueltos.length > 0 && (
        <ul className="prosa-lista">
          {bloque.sueltos.map((linea, j) => (
            <li key={j}>{negritas(linea)}</li>
          ))}
        </ul>
      )}
    </>
  )
}

/** `**así**` pasa a <strong>, sin tocar el resto. */
function negritas(texto) {
  return String(texto)
    .split(/(\*\*[^*]+\*\*)/g)
    .map((trozo, i) =>
      trozo.startsWith('**') && trozo.endsWith('**') && trozo.length > 4 ? (
        <strong key={i}>{trozo.slice(2, -2)}</strong>
      ) : (
        trozo
      ),
    )
}
