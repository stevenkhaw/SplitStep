import { svelte } from '@sveltejs/vite-plugin-svelte'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig(({ mode }) => ({
  plugins: [svelte(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8420',
      '/media': 'http://127.0.0.1:8420',
    },
  },
  // Vitest resolves packages under Node's default export conditions, which
  // point `svelte` at its server-rendering build (mount() throws "not
  // available on the server" there). Forcing the `browser` condition in
  // test mode is the fix the Svelte docs themselves prescribe for component
  // tests -- but setting `resolve.conditions` to anything, even `[]`,
  // replaces Vite's own default condition list rather than extending it, so
  // this key is omitted entirely outside test mode rather than set to a
  // no-op value: `vite build`/`vite dev` must see exactly the config they
  // saw before this existed.
  ...(mode === 'test' ? { resolve: { conditions: ['browser'] } } : {}),
  test: { environment: 'jsdom' },
}))
