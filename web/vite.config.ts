import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8420',
      '/media': 'http://127.0.0.1:8420',
    },
  },
  test: { environment: 'jsdom' },
})
