import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Separate from vite.config.ts on purpose: web/dist is copied into the
// PyInstaller bundle and must keep producing exactly the files it does
// today, so the launcher gets its own output directory rather than an extra
// entry alongside index.html.
export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  build: {
    outDir: 'dist-launcher',
    emptyOutDir: true,
    rollupOptions: { input: 'launcher.html' },
  },
  server: { port: 5174 },
})
