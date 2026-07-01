import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: proxy /api to the FastAPI server. Build: static assets the server mounts.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8001',
    },
  },
  build: { outDir: 'dist' },
})
