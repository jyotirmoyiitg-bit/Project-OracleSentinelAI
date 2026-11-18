import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The API the dev server forwards to. Set SENTINEL_API to preview against a backend on another port.
const api = process.env.SENTINEL_API ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': api,
      '/health': api,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    css: true,
  },
})
