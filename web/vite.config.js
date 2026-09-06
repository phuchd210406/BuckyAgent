import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8000 (`make api`). Proxying in dev keeps the browser on one
// origin, so EventSource needs no CORS and the backend needs no changes.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/runs': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/config': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
