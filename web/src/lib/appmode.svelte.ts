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

export const appmode = {
  get current() {
    return current
  },
  async load(): Promise<void> {
    try {
      current = (await api.config()).mode
    } catch {
      // An unreachable server already surfaces through every route's own
      // error note; the mode flag failing must not add a second banner.
    }
  },
  async set(mode: AppMode): Promise<void> {
    // Optimistic: the toggle is the only writer, and the gates it flips are
    // client-side renders -- waiting a round-trip to hide a panel would make
    // the checkbox feel broken.
    current = mode
    await api.setMode(mode)
  },
}
