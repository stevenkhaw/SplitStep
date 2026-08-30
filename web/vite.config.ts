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
  //
  // `environments.client` is a second test-only shim, and it exists because
  // of a version skew rather than anything either tool got wrong. Vitest 2
  // creates its server with its own bundled Vite 5, but `vitePreprocess()` --
  // which is what runs a component's <style> block through the CSS pipeline
  // -- imports `preprocessCSS` from the project's Vite 6. Vite 6 wraps the
  // config it is handed in `new PartialEnvironment('client', config)`, whose
  // constructor proxies `config.environments.client`; a Vite 5 resolved
  // config carries only `ssr`, so the proxy target is `undefined` and every
  // component with a <style> block dies at compile time with "Cannot create
  // proxy with a non-object as target or handler" -- regardless of what the
  // CSS says. An empty client environment is the whole fix: that proxy falls
  // through to the top-level config for anything the environment does not
  // override, which is exactly what Vite 5 would have done on its own.
  // `vite build` never reaches this path -- there vite-plugin-svelte injects
  // the live resolved config, which has a real client environment -- which is
  // why the build compiled scoped styles correctly the entire time this was
  // broken, and why nothing but a test could catch it.
  //
  // Upgrading the plugin does not help and was measured, not assumed: v6 and
  // v7 ship a byte-identical style preprocessor, and v6 additionally calls
  // `Object.values(server.environments)` from configureServer, which throws
  // on vitest 2's Vite 5 server before a single test collects. The end of
  // this shim is a vitest major that bundles Vite 6 or newer; delete the key
  // then, and let tests/scoped-style.test.ts say whether it was load-bearing.
  ...(mode === 'test'
    ? { resolve: { conditions: ['browser'] }, environments: { client: {} } }
    : {}),
  test: { environment: 'jsdom' },
}))
