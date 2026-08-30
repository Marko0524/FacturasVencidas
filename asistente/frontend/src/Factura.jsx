import { IconoFactura } from './icons.jsx'

/**
 * Una factura, presentada como una factura.
 *
 * Antes esto era una tabla monoespaciada dentro de un globo de chat, que es lo
 * que hace que un producto de cobranza parezca un chatbot genérico. Los datos
 * vienen estructurados del backend, no parseados del texto: la prosa y esta
 * tarjeta salen del mismo registro, así que no pueden contradecirse.
 */

function Medidor({ dias, umbral }) {
  // La barra no mide días absolutos sino distancia al umbral: "26 días" no
  // significa nada sin la línea que cruzó.
  const tope = Math.max(umbral * 2, dias, 1)
  const porcentaje = Math.min((dias / tope) * 100, 100)
  const marca = Math.min((umbral / tope) * 100, 100)
  const rebasa = dias > umbral

  return (
    <div className="medidor">
      <div
        className="medidor-pista"
        role="img"
        aria-label={`${dias} días de atraso; el umbral de escalamiento son ${umbral} días`}
      >
        <span
          className={`medidor-relleno${rebasa ? ' rebasado' : ''}`}
          style={{ width: `${porcentaje}%` }}
        />
        <span className="medidor-umbral" style={{ left: `${marca}%` }} />
      </div>
      <div className="medidor-pie">
        <span>{dias === 1 ? '1 día' : `${dias} días`} de atraso</span>
        <span className="medidor-marca">umbral {umbral}</span>
      </div>
    </div>
  )
}

export function TarjetaFactura({ datos }) {
  const { id, importe_texto, vencimiento_texto, estatus, estatus_texto } = datos

  return (
    <article className={`factura factura-${estatus}`}>
      <header className="factura-cabecera">
        <span className="factura-id">
          <IconoFactura />
          {id}
        </span>
        {/* El estatus como sello, no como una celda más de una tabla. */}
        <span className={`sello sello-${estatus}`}>{estatus_texto}</span>
      </header>

      <p className="factura-importe">{importe_texto}</p>

      <dl className="factura-datos">
        <div>
          <dt>Vencimiento</dt>
          <dd>{vencimiento_texto}</dd>
        </div>
        {datos.dias_restantes > 0 && (
          <div>
            <dt>Vence en</dt>
            <dd>{datos.dias_restantes === 1 ? '1 día' : `${datos.dias_restantes} días`}</dd>
          </div>
        )}
      </dl>

      {datos.vencida && <Medidor dias={datos.dias_atraso} umbral={datos.umbral_dias} />}
    </article>
  )
}

export function TarjetaCuenta({ datos }) {
  const { pendientes, total, moneda, vencidas, facturas } = datos
  const formato = new Intl.NumberFormat('es-MX', { minimumFractionDigits: 2 })

  return (
    <div className="cuenta">
      <div className="cuenta-resumen">
        <span className="cuenta-etiqueta">Saldo pendiente</span>
        <p className="cuenta-total">
          ${formato.format(total)} <span className="cuenta-moneda">{moneda}</span>
        </p>
        <p className="cuenta-detalle">
          {pendientes === 1 ? '1 factura pendiente' : `${pendientes} facturas pendientes`}
          {vencidas > 0 && (
            <>
              {' · '}
              <strong className="cuenta-vencidas">
                {vencidas === 1 ? '1 vencida' : `${vencidas} vencidas`}
              </strong>
            </>
          )}
        </p>
      </div>

      <ul className="cuenta-lista">
        {facturas.map((f) => (
          <li key={f.id} className={f.vencida ? 'vencida' : ''}>
            <span className="mono">{f.id}</span>
            <span className="cuenta-importe">{f.importe_texto}</span>
            <span className="cuenta-fecha">
              {f.vencida
                ? `vencida hace ${f.dias_atraso} d.`
                : `vence ${f.vencimiento_texto}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
