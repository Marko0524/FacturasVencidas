import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Escucha en todas las interfaces. Sin esto Vite se ata solo a lo que
    // resuelva `localhost`, que en Windows es ::1, y abrir la misma página por
    // 127.0.0.1 da conexión rechazada — con la trampa de que una pestaña vieja
    // sigue mostrando la versión anterior en vez de fallar de forma visible.
    host: true,
    // Vite comprueba la cabecera Host y responde 403 a cualquiera que no sea
    // localhost o una IP, que es lo que impide que una página cualquiera de
    // internet hable con este servidor por DNS rebinding. Un túnel llega
    // justamente con otro Host, así que sin esta línea la URL pública devuelve
    // "Blocked request" en vez de la interfaz.
    //
    // El punto inicial autoriza el dominio entero y no un identificador: el
    // túnel cambia de nombre cada vez que se recrea, y anclar el de hoy
    // convertiría eso en un 403 que nadie relaciona con este archivo.
    allowedHosts: ['.devtunnels.ms'],
    // El backend responde en 8000. Con el proxy, el navegador solo habla con
    // un origen y no hace falta pensar en CORS durante el desarrollo.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
