import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * Address of the CodingPass FastAPI judge (`backend/main.py`).
 *
 * Everything under `/api` is proxied to it, so the browser only ever talks to
 * the Vite origin and the backend never needs CORS during development.
 * Override for an unusual setup, e.g.
 * `CODEPASS_API_TARGET=http://127.0.0.1:8010 npm run dev`.
 */
const API_TARGET = process.env.CODEPASS_API_TARGET ?? 'http://127.0.0.1:8010'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  // Same proxying for `npm run preview`, so a production build can be smoke
  // tested locally without deploying the API on the same origin.
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
})
