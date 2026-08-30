import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// Resolved via fileURLToPath + path.join rather than `new URL('../src/...',
// import.meta.url)` directly: this project's vite.config.ts forces
// `resolve.conditions: ['browser']` in test mode (so Svelte resolves its
// browser build under vitest), which makes Vite's static asset-URL plugin
// treat the literal `new URL(<string>, import.meta.url)` pattern as a build
// asset reference and rewrite it against jsdom's default document location
// instead of the file's real path -- `fileURLToPath` then rejects the
// resulting `http:` URL. Splitting the two calls sidesteps the plugin's
// syntactic match while reading the exact same file.
const LIBRARY = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../src/routes/Library.svelte'),
  'utf8',
)

describe('the session list', () => {
  // A 152px thumbnail alone on a 3400px row is the ultrawide complaint in
  // miniature: the card has to fill the space or stop claiming it.
  it('goes two-up once there is room', () => {
    expect(LIBRARY).toMatch(/grid-cols-1/)
    expect(LIBRARY).toMatch(/min-\[1800px\]:grid-cols-2/)
  })

  it('keeps the star and point counts coloured', () => {
    expect(LIBRARY).toMatch(/text-point/)
    expect(LIBRARY).toMatch(/text-star/)
  })
})
