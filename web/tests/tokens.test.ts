import { readdirSync, readFileSync, statSync } from 'node:fs'
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

/** Every `--color-<name>: #rrggbb` declared in the theme block. */
function tokens(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of CSS.matchAll(/--color-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    out[m[1]] = m[2].toLowerCase()
  }
  return out
}

function channel(v: number): number {
  const c = v / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const n = hex.replace('#', '')
  const [r, g, b] = [0, 2, 4].map((i) => channel(parseInt(n.slice(i, i + 2), 16)))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG 2.x contrast ratio. */
export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

const GROUNDS = ['bg', 'surface', 'surface-2'] as const

describe('the palette', () => {
  it('declares every token the app depends on', () => {
    const t = tokens()
    for (const name of [
      'bg', 'surface', 'surface-2', 'line',
      'fg', 'dim', 'faint',
      'star', 'point', 'danger',
      'court', 'court-run', 'court-line', 'ball',
    ]) {
      expect(t[name], `--color-${name} is missing`).toBeDefined()
    }
  })

  it('has no accent token — the chrome carries no hue', () => {
    expect(tokens()['accent']).toBeUndefined()
  })

  // Text must clear 4.5:1 on every ground it can land on. `faint` carries the
  // keyboard legend, which is functional text, so it is held to the same bar
  // as body copy -- this is the check that would have caught #74747F in the
  // last redesign and #7186a0 in this one.
  it.each(['fg', 'dim', 'faint'])('%s clears 4.5:1 on every ground', (text) => {
    const t = tokens()
    for (const ground of GROUNDS) {
      expect(
        contrast(t[text], t[ground]),
        `--color-${text} on --color-${ground}`,
      ).toBeGreaterThanOrEqual(4.5)
    }
  })

  it.each(['star', 'point', 'danger'])('%s clears 3:1 as a graphical mark', (mark) => {
    const t = tokens()
    for (const ground of GROUNDS) {
      expect(contrast(t[mark], t[ground])).toBeGreaterThanOrEqual(3.0)
    }
  })

  // Not a contrast ratio: star and point are deliberately the same lightness
  // (0.62 and 0.67) and opposite hues (34° and 192°), which is what "warm
  // versus cool keeps them apart at a glance" means. A luminance ratio would
  // measure the property they were designed to share. The pair that had to be
  // ruled out was a cyan accent against point -- same hue AND same lightness --
  // and that accent no longer exists.
  it('keeps star and point on opposite sides of the wheel', () => {
    const t = tokens()
    const hue = (hex: string): number => {
      const n = hex.replace('#', '')
      const [r, g, b] = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16) / 255)
      const max = Math.max(r, g, b)
      const min = Math.min(r, g, b)
      if (max === min) return 0
      const d = max - min
      const h =
        max === r ? ((g - b) / d + (g < b ? 6 : 0)) : max === g ? (b - r) / d + 2 : (r - g) / d + 4
      return h * 60
    }
    const apart = Math.abs(hue(t['star']) - hue(t['point']))
    expect(Math.min(apart, 360 - apart)).toBeGreaterThanOrEqual(90)
  })

  // fg is the only token permitted over bare court (5.27:1). dim and faint
  // measure 2.41 and 2.09 there, which is why the layout rule exists.
  it('lets fg cross bare court, and only fg', () => {
    const t = tokens()
    expect(contrast(t['fg'], t['court'])).toBeGreaterThanOrEqual(4.5)
    expect(contrast(t['dim'], t['court'])).toBeLessThan(4.5)
  })

  it('keeps the ball legible on the icon ground', () => {
    const t = tokens()
    expect(contrast(t['ball'], t['court'])).toBeGreaterThanOrEqual(3.0)
    expect(contrast(t['ball'], t['bg'])).toBeGreaterThanOrEqual(3.0)
  })
})

function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) sourceFiles(full, acc)
    else if (/\.(svelte|ts)$/.test(entry)) acc.push(full)
  }
  return acc
}

describe('no callsite still reaches for accent', () => {
  // Deleting the token is only half of it: a `bg-accent` left behind resolves
  // to nothing and fails at runtime as an unstyled element, not at build.
  it('has no accent class anywhere in src', () => {
    const root = join(dirname(fileURLToPath(import.meta.url)), '../src')
    const guilty: string[] = []
    for (const file of sourceFiles(root)) {
      const text = readFileSync(file, 'utf8')
      if (/\b(bg|text|border|ring|accent|fill|stroke)-accent\b/.test(text)) {
        guilty.push(file.slice(root.length + 1))
      }
    }
    expect(guilty).toEqual([])
  })
})
