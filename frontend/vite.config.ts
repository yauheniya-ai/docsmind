import path from 'path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// The backend (FastAPI, see docsmind/src/docsmind/server) runs on its own
// port in dev (`docsmind serve`, default 8000). Proxy `/api` and `/blobs`
// so the frontend can call relative paths with no CORS dance.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'
const bundledUiOutDir = path.resolve(import.meta.dirname, '../src/docsmind/server/ui')

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: bundledUiOutDir,
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/blobs': { target: apiTarget, changeOrigin: true },
    },
  },
})
