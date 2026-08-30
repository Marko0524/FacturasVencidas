import { useEffect, useRef, useState } from 'react'
import { IconoEscudo } from './icons.jsx'

const GSI = 'https://accounts.google.com/gsi/client'

// El script de Google se carga una sola vez aunque el componente se remonte.
let cargandoGoogle = null

function cargarGoogle() {
  if (window.google?.accounts?.id) return Promise.resolve()
  if (cargandoGoogle) return cargandoGoogle

  cargandoGoogle = new Promise((resolver, rechazar) => {
    const s = document.createElement('script')
    s.src = GSI
    s.async = true
    s.defer = true
    s.onload = resolver
    s.onerror = () => rechazar(new Error('no se pudo cargar Google Sign-In'))
    document.head.appendChild(s)
  })
  return cargandoGoogle
}

/** Botón de Google, cuando el modo de autenticación es `google`. */
function BotonGoogle({ clientId, onCredencial, onFallo }) {
  const botonRef = useRef(null)

  useEffect(() => {
    let vivo = true
    cargarGoogle()
      .then(() => {
        if (!vivo || !botonRef.current) return
        window.google.accounts.id.initialize({
          client_id: clientId,
          // El token va al backend y se verifica ahí. Lo que decida el
          // navegador sobre quién eres no es una credencial, es una opinión.
          callback: (respuesta) => onCredencial(respuesta.credential),
          auto_select: false,
        })
        window.google.accounts.id.renderButton(botonRef.current, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          text: 'signin_with',
          shape: 'pill',
          locale: 'es',
          width: 300,
        })
      })
      .catch((e) => vivo && onFallo(e.message))
    return () => {
      vivo = false
    }
  }, [clientId, onCredencial, onFallo])

  return <div ref={botonRef} className="acceso-boton" />
}

/** Formulario de correo y contraseña, cuando el modo es `local`. */
function FormularioLocal({ onEntrar, ocupado }) {
  const [correo, setCorreo] = useState('')
  const [contrasena, setContrasena] = useState('')

  return (
    <form
      className="acceso-formulario"
      onSubmit={(e) => {
        e.preventDefault()
        onEntrar(correo, contrasena)
      }}
    >
      <div className="campo-acceso">
        <label htmlFor="correo">Correo</label>
        <input
          id="correo"
          type="email"
          value={correo}
          onChange={(e) => setCorreo(e.target.value)}
          // El navegador y los gestores de contraseñas necesitan estas pistas
          // para ofrecer las credenciales guardadas.
          autoComplete="username"
          required
          disabled={ocupado}
          placeholder="tu@correo.com"
        />
      </div>

      <div className="campo-acceso">
        <label htmlFor="contrasena">Contraseña</label>
        <input
          id="contrasena"
          type="password"
          value={contrasena}
          onChange={(e) => setContrasena(e.target.value)}
          autoComplete="current-password"
          required
          disabled={ocupado}
        />
      </div>

      <button type="submit" className="enviar acceso-enviar" disabled={ocupado}>
        {ocupado ? 'Verificando…' : 'Entrar'}
      </button>
    </form>
  )
}

export default function Acceso({ modo, clientId, onCredencial, onEntrar, ocupado, error }) {
  const [fallo, setFallo] = useState('')

  return (
    <div className="acceso">
      <div className="lienzo" aria-hidden="true" />

      <main className="acceso-tarjeta">
        <span className="marca-glifo grande">
          <IconoEscudo />
        </span>

        <h1>Asistente de pólizas y facturación</h1>
        <p className="acceso-texto">
          Inicie sesión para consultar sus pólizas y el estado de sus facturas.
          Solo verá la documentación de su cuenta.
        </p>

        {modo === 'google' ? (
          <BotonGoogle clientId={clientId} onCredencial={onCredencial} onFallo={setFallo} />
        ) : (
          <FormularioLocal onEntrar={onEntrar} ocupado={ocupado} />
        )}

        {/* El resultado de un intento fallido tiene que anunciarse, no solo verse. */}
        <div aria-live="polite">
          {(fallo || error) && (
            <p className="aviso aviso-error" role="alert">
              {fallo || error}
            </p>
          )}
        </div>

        <p className="acceso-pie">
          {modo === 'google'
            ? 'El token se verifica en el servidor contra las llaves públicas de Google.'
            : 'Las contraseñas se guardan con scrypt y sal por usuario; el servidor nunca almacena la contraseña.'}{' '}
          La sesión vive solo en esta pestaña.
        </p>
      </main>
    </div>
  )
}
