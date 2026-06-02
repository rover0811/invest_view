import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  base: '/',
  plugins: [svelte()],
  server: {
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
})
