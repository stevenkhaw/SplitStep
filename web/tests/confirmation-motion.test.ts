import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// Resolved via fileURLToPath + path.join rather than `new URL('../src/app.css',
// import.meta.url)` directly: this project's vite.config.ts forces
// `resolve.conditions: ['browser']` in test mode (so Svelte resolves its
// browser build under vitest), which makes Vite's static asset-URL plugin
// treat the literal `new URL(<string>, import.meta.url)` pattern as a build
// asset reference and rewrite it against jsdom's default document location
// instead of the file's real path -- `fileURLToPath` then rejects the
// resulting `http:` URL. Splitting the two calls sidesteps the plugin's
// syntactic match while reading the exact same file.
const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '../src/app.css'),
  'utf8',
)

describe('motion', () => {
  // The namespace matters: a token declared as `--duration-quick` generates
  // no utility and is dropped from the compiled output without an error,
  // which is a failure with nothing to notice.
  it('names its durations in the namespace Tailwind actually reads', () => {
    expect(CSS).toMatch(/--transition-duration-quick:/)
    expect(CSS).toMatch(/--transition-duration-calm:/)
    expect(CSS).toMatch(/--transition-duration-slow:/)
    expect(CSS).toMatch(/--ease-out-soft:/)
    expect(CSS).not.toMatch(/--duration-(quick|calm|slow):/)
  })

  // Tailwind drops theme variables no utility references, and the counter's
  // animate-[…] names these directly rather than through one.
  it('declares them statically so the animations can name them', () => {
    expect(CSS).toMatch(/@theme static/)
  })

  // Star, point and reject are pressed hundreds of times a session. A
  // confirmation still on screen when the next keypress lands reads as one
  // long event rather than two, which is the bug the original verdict
  // keyframe was written to avoid.
  it('keeps every confirmation under 200ms', () => {
    const quick = CSS.match(/--transition-duration-quick:\s*(\d+)ms/)
    expect(Number(quick?.[1])).toBeLessThan(200)
  })

  it('gives the counter its own keyframe', () => {
    expect(CSS).toMatch(/@keyframes counter-roll/)
  })
})
