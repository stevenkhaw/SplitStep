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
const SRC = join(dirname(fileURLToPath(import.meta.url)), '../src')
const LIBRARY = readFileSync(join(SRC, 'routes/Library.svelte'), 'utf8')
const REELS = readFileSync(join(SRC, 'routes/Reels.svelte'), 'utf8')
const THUMB = readFileSync(join(SRC, 'components/Thumb.svelte'), 'utf8')
const APP_CSS = readFileSync(join(SRC, 'app.css'), 'utf8')

describe('the session list', () => {
  // A 152px thumbnail alone on a 3400px row is the ultrawide complaint in
  // miniature: the card has to fill the space or stop claiming it.
  it('goes two-up once there is room', () => {
    expect(LIBRARY).toMatch(/grid-cols-1/)
    expect(LIBRARY).toMatch(/\bultra:grid-cols-2\b/)
  })

  it('keeps the star and point counts coloured', () => {
    expect(LIBRARY).toMatch(/text-point/)
    expect(LIBRARY).toMatch(/text-star/)
  })
})

// The grid going two-up and the thumbnail growing are one visual change, not
// two -- a card whose thumbnail jumped at a different width than its row
// would look broken even though each file's own tests still pass. Pinning
// all three call sites to one named token, and asserting none of them has
// regressed to a literal pixel value, is what would actually catch a
// `Thumb.svelte` left behind on a re-tune -- a shared regex string could not.
describe('the ultrawide breakpoint', () => {
  it('is declared once, as a token, not repeated as a literal', () => {
    expect(APP_CSS).toMatch(/--breakpoint-ultra:\s*1800px/)
  })

  it('is what the session list, the reel list and the thumbnail all key off', () => {
    expect(LIBRARY).toMatch(/\bultra:grid-cols-2\b/)
    expect(REELS).toMatch(/\bultra:grid-cols-2\b/)
    expect(THUMB).toMatch(/\bultra:w-70\b/)
  })

  it('never reappears as a hardcoded min-[…px] at any of the three call sites', () => {
    for (const src of [LIBRARY, REELS, THUMB]) {
      expect(src).not.toMatch(/min-\[\d+px\]/)
    }
  })
})
