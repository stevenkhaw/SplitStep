import { flushSync, mount, unmount } from 'svelte'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

// vitePreprocess()'s style stage used to crash every component carrying a
// <style> block under vitest -- "Cannot create proxy with a non-object as
// target or handler", thrown by Vite's PartialEnvironment when the standalone
// resolveConfig() vite-plugin-svelte falls back to has no environments.client.
// `vite build` was unaffected, because there the live resolved config is
// injected instead, so nothing in the build or the type check could see it.
// This test mounts a component whose only distinguishing feature is a scoped
// <style>; if the preprocessor regresses, it fails at import time.
const { default: ScopedStyle } = await import('./support/ScopedStyle.svelte')

describe('a component with a scoped <style> block', () => {
  let target: HTMLDivElement
  let instance: unknown

  beforeEach(() => {
    target = document.createElement('div')
    document.body.appendChild(target)
  })

  afterEach(() => {
    if (instance) unmount(instance as never)
    target.remove()
    instance = undefined
  })

  it('compiles and mounts', () => {
    instance = mount(ScopedStyle, { target })
    flushSync()
    expect(target.querySelector('.probe')?.textContent).toBe('scoped')
  })

  it('gets a scoping class on the styled element', () => {
    instance = mount(ScopedStyle, { target })
    flushSync()
    const el = target.querySelector('.probe')!
    // Svelte scopes by adding an svelte-<hash> class alongside the author's.
    expect([...el.classList].some((c) => c.startsWith('svelte-'))).toBe(true)
  })
})
