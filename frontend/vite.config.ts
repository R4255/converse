import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT || 5173),
    // API_URL lets Converse run next to other local projects that already use :8000
    proxy: { '/api': { target: process.env.API_URL || 'http://localhost:8000', changeOrigin: true } },
  },
})
