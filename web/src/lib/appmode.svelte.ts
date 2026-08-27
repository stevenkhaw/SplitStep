import { api } from './api'
import type { AppMode } from './types'

/**
 * The client's mirror of the server's mode flag, loaded once at boot.
 *
 * 'dev' until the config request lands: a dev checkout is the only place the
 * UI runs without the config having been written, and hiding the tuning
 * tools for one round-trip would flash the chrome at the one person who
 * notices. A friend's config says 'friend' before the app ever opens, and
 * entering the gated surfaces takes a deliberate keypress regardless -- the
 * flash window cannot cost a corpus write.
 */
let current = $state<AppMode>('dev')

const RETRY_MS = 5000

export const appmode = {
  get current() {
    return current
  },
  async load(): Promise<void> {
    try {
      current = (await api.config()).mode
    } catch {
      // The server may be mid-restart. Giving up would fail open to 'dev'
      // for the whole page load -- on a friend install that exposes the
      // tuning tools this flag exists to hide -- so keep asking until an
      // answer arrives.
      setTimeout(() => void appmode.load(), RETRY_MS)
    }
  },
  async set(mode: AppMode): Promise<void> {
    // Optimistic, but reverted on failure: the toggle is the only writer,
    // and a checkbox that snaps back is the honest signal that the write
    // did not stick -- silently keeping the new mode would let the client
    // and the config file disagree until the next boot.
    const before = current
    current = mode
    try {
      await api.setMode(mode)
    } catch {
      current = before
    }
  },
}
