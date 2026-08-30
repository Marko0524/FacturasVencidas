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
    // El backend responde en 8000. Con el proxy, el navegador solo habla con
    // un origen y no hace falta pensar en CORS durante el desarrollo.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
