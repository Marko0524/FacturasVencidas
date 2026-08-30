import { IconoLuna, IconoSol } from './icons.jsx'

/**
 * Un solo botón, que muestra el tema al que se va, no el que está puesto.
 *
 * Por eso el nombre accesible dice la acción y no el estado: un icono de luna
 * podría leerse como "estás en oscuro" o como "cambia a oscuro", y a un lector
 * de pantalla hay que decirle cuál de las dos.
 */
export default function Tema({ tema, onAlternar }) {
  const vaOscuro = tema === 'claro'
  const Icono = vaOscuro ? IconoLuna : IconoSol
  const etiqueta = vaOscuro ? 'Cambiar a modo oscuro' : 'Cambiar a modo claro'

  return (
    <button type="button" className="tema" onClick={onAlternar} title={etiqueta}>
      <Icono />
      <span className="oculto">{etiqueta}</span>
    </button>
  )
}
