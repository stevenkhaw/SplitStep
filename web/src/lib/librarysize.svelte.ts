import { api } from './api'

/**
 * The library-size figure, cached at module scope.
 *
 * The server computes it with a full walk of the library tree -- cheap warm,
 * seconds when an external drive's metadata cache is cold -- and the Library
 * route remounts on every hash navigation. Component-local state re-walked
 * the drive and blanked the header on every return; this keeps the last
 * value on screen (stale-while-revalidate) and makes each visit cost one
 * background refresh instead of a blocking blink.
 */
let bytes = $state<number | null>(null)

export const librarySize = {
  get bytes() {
    return bytes
  },
  async refresh(): Promise<void> {
    try {
      bytes = (await api.libraryStats()).bytes
    } catch {
      // Keep the stale figure; the header simply doesn't update. A missing
      // drive surfaces through the session list's own error path, not here.
    }
  },
}
