import { mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AppBar from '../src/components/AppBar.svelte'

// The bar reads the drive figure from a store, not from `api` directly --
// librarysize.svelte.ts is stale-while-revalidate so the header does not
// blank on every navigation.
vi.mock('../src/lib/librarysize.svelte', () => ({
  librarySize: { bytes: 35_433_480_192, refresh: vi.fn(async () => {}) },
}))

vi.mock('../src/lib/api', () => ({
  api: { jobs: vi.fn(async () => []), config: vi.fn(async () => ({ mode: 'dev' })) },
}))

vi.mock('../src/lib/shell', () => ({
  inShell: () => false,
  changeLibrary: vi.fn(async () => {}),
}))

let host: HTMLElement | null = null
let app: Record<string, unknown> | null = null

beforeEach(() => {
  window.location.hash = '#/'
})

afterEach(() => {
  if (app) unmount(app)
  host?.remove()
  app = null
  host = null
})

function render() {
  host = document.createElement('div')
  document.body.appendChild(host)
  app = mount(AppBar, { target: host, props: {} })
  return host
}

describe('AppBar', () => {
  // Reels used to be reachable only from Library -- a link inside one
  // route's header, sitting at the same weight as a storage figure.
  it('offers Sessions and Reels as peers', () => {
    const el = render()
    const labels = [...el.querySelectorAll('nav a, nav button')].map((n) => n.textContent?.trim())
    expect(labels).toContain('Sessions')
    expect(labels).toContain('Reels')
  })

  it('marks the current route', () => {
    const el = render()
    const current = el.querySelector('[aria-current="page"]')
    expect(current?.textContent?.trim()).toBe('Sessions')
  })

  it('carries the mark', () => {
    const el = render()
    expect(el.querySelector('svg circle')).not.toBeNull()
  })
})
