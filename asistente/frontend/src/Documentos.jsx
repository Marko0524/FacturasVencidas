import { IconoDescargar, IconoDocumento, IconoEliminar } from './icons.jsx'

/**
 * La lista de la barra lateral. Solo presenta: el estado y las acciones viven
 * en `useDocumentos`, porque el botón de subir está ahora en el redactor y los
 * dos tienen que ver exactamente lo mismo.
 */
export default function Documentos({
  documentos,
  disponible,
  activo,
  onSeleccionar,
  onDescargar,
  onEliminar,
}) {
  if (!disponible) {
    return (
      <section className="documentos">
        <span className="bloque-titulo">Documentos</span>
        <p className="pista">
          La carga de documentos necesita el almacén en Postgres. Levántalo con{' '}
          <code>docker compose up -d</code> y arranca el backend con{' '}
          <code>RETRIEVAL_BACKEND=postgres</code>.
        </p>
      </section>
    )
  }

  const propios = documentos.filter((d) => d.origen === 'carga')
  const base = documentos.filter((d) => d.origen !== 'carga')

  // La frontera de permisos, hecha visible desde el lado correcto.
  //
  // Lo obvio sería mostrar "2 documentos restringidos", y sería un error: ese
  // número confirma que existen documentos de otros clientes y cuántos. Es una
  // fuga pequeña, pero este proyecto entero trata de no tenerlas. Así que se
  // describe el ÁMBITO propio, que informa lo mismo y no revela nada de nadie.
  const publicos = documentos.filter((d) => d.alcance === 'publico').length
  const decuenta = documentos.length - publicos

  return (
    <section className="documentos">
      <div className="documentos-cabecera">
        <span className="bloque-titulo">Ámbito de consulta</span>
        <span className="cuenta">{documentos.length}</span>
      </div>

      <div className="ambito">
        <span className="ambito-parte">
          <span className="ambito-cifra">{publicos}</span>
          públicos
        </span>
        <span className="ambito-division" aria-hidden="true" />
        <span className="ambito-parte">
          <span className="ambito-cifra">{decuenta}</span>
          de su cuenta
        </span>
      </div>

      {propios.length > 0 && (
        <>
          <p className="grupo">Tuyos</p>
          <ul className="lista">
            {propios.map((d) => (
              <li key={d.nombre} className={activo === d.nombre ? 'seleccionado' : ''}>
                <button
                  type="button"
                  className="doc-elegir"
                  onClick={() => onSeleccionar(d)}
                  aria-pressed={activo === d.nombre}
                >
                  <IconoDocumento />
                  <span className="doc-texto">
                    <span className="doc-titulo">{d.titulo}</span>
                    <span className="doc-meta">{d.fragmentos} fragmentos</span>
                  </span>
                </button>
                <span className="doc-acciones">
                  <button
                    type="button"
                    className="eliminar"
                    onClick={() => onDescargar(d.nombre, d.titulo)}
                    aria-label={`Abrir ${d.titulo} en PDF`}
                  >
                    <IconoDescargar />
                  </button>
                  <button
                    type="button"
                    className="eliminar"
                    onClick={() => onEliminar(d.nombre, d.titulo)}
                    aria-label={`Eliminar ${d.titulo}`}
                  >
                    <IconoEliminar />
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <p className="grupo">De la aseguradora</p>
      <ul className="lista">
        {base.map((d) => (
          <li key={d.nombre} className={activo === d.nombre ? 'seleccionado' : ''}>
            <button
              type="button"
              className="doc-elegir"
              onClick={() => onSeleccionar(d)}
              aria-pressed={activo === d.nombre}
            >
              <IconoDocumento />
              <span className="doc-texto">
                <span className="doc-titulo">{d.titulo}</span>
                <span className="doc-meta">
                  {d.alcance === 'cliente' ? 'Solo suyo' : 'Público'} · {d.fragmentos} fragmentos
                </span>
              </span>
            </button>
            <span className="doc-acciones">
              <button
                type="button"
                className="eliminar"
                onClick={() => onDescargar(d.nombre, d.titulo)}
                aria-label={`Abrir ${d.titulo} en PDF`}
              >
                <IconoDescargar />
              </button>
            </span>
          </li>
        ))}
      </ul>

      <p className="pista">
        Toca un documento para consultarlo directamente —por ejemplo, para pedir
        un resumen. Este es todo su ámbito. Lo de otros clientes no se lista ni se busca: el
        filtro va dentro de la misma consulta SQL que ordena por distancia, así
        que un documento ajeno nunca llega a puntuarse.
      </p>
    </section>
  )
}
