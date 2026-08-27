import { afterEach, describe, expect, it, vi } from 'vitest'
import { changeLibrary, inShell } from '../src/lib/shell'

const g = globalThis as Record<string, unknown>

afterEach(() => {
  delete g.__SPLITSTEP_BRIDGE__
})

describe('inShell', () => {
  it('is false in a plain browser', () => {
    expect(inShell()).toBe(false)
  })

  it('is true when the shell injected its bridge', () => {
    g.__SPLITSTEP_BRIDGE__ = { backToChooser: async () => {} }
    expect(inShell()).toBe(true)
  })
})

describe('changeLibrary', () => {
  it('asks the shell to go back to the chooser', async () => {
    const backToChooser = vi.fn().mockResolvedValue(undefined)
    g.__SPLITSTEP_BRIDGE__ = { backToChooser }
    await changeLibrary()
    expect(backToChooser).toHaveBeenCalledOnce()
  })

  it('throws a sentence, not undefined, outside the shell', async () => {
    // The browser tier has no shell to ask and hides the button -- but a
    // stale tab could still reach this, and it must say something.
    await expect(changeLibrary()).rejects.toThrow(/desktop app/i)
  })

  it('lets a shell-side failure surface rather than swallowing it', async () => {
    g.__SPLITSTEP_BRIDGE__ = {
      backToChooser: async () => {
        throw new Error('no main window')
      },
    }
    await expect(changeLibrary()).rejects.toThrow('no main window')
  })
})
