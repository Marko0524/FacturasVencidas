import { useCallback, useState } from 'react'

/**
 * Claro u oscuro, nada más.
 *
 * La preferencia del sistema sigue decidiendo el arranque —es el mejor valor
 * inicial que hay— pero deja de ser un estado de la interfaz: en cuanto alguien
 * elige, esa elección manda y se guarda.
 */
const CLAVE = 'tema'

function temaInicial() {
  try {
    const guardado = localStorage.getItem(CLAVE)
    if (guardado === 'claro' || guardado === 'oscuro') return guardado
  } catch {
    // Ventana privada o almacenamiento bloqueado: no hay preferencia guardada,
    // que no es lo mismo que un error.
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'oscuro' : 'claro'
}

export function useTema() {
  // El atributo ya lo puso el script de index.html antes del primer pintado;
  // aquí solo se lee lo mismo para que React y el DOM no discrepen.
  const [tema, setTema] = useState(temaInicial)

  const alternar = useCallback(() => {
    setTema((actual) => {
      const nuevo = actual === 'oscuro' ? 'claro' : 'oscuro'
      document.documentElement.dataset.tema = nuevo
      try {
        localStorage.setItem(CLAVE, nuevo)
      } catch {
        // Sin persistencia el tema vale para esta pestaña. Peor sería romper.
      }
      return nuevo
    })
  }, [])

  return { tema, alternar }
}
