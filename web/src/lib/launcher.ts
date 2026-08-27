import { formatBytes } from './reels'

export type LibraryEntry = {
  path: string
  reachable: boolean
  hasLibrary: boolean
}

export type ChooserState = 'first-run' | 'not-connected' | 'switching'

/** Which of the three reasons brought us to the chooser.
 *
 * The chooser is skipped on a normal launch -- the shell boots straight into
 * a configured, reachable library -- so arriving here always means one of
 * exactly three things, and the copy differs for each. `switching` is
 * inferred rather than passed: a reachable configured library would have
 * been booted into, so a human must have asked for the chooser from
 * Settings.
 */
export function chooserState(
  known: LibraryEntry[],
  configured: string | null,
): ChooserState {
  if (!configured) return 'first-run'
  const match = known.find((entry) => entry.path === configured)
  return match?.reachable ? 'switching' : 'not-connected'
}

export function chooserCopy(
  state: ChooserState,
  configured: string | null,
): { heading: string; body: string } {
  if (state === 'not-connected') {
    return {
      heading: 'Your library is not connected',
      body:
        `SplitStep last used ${configured}, which is not available right now. ` +
        'Plug the drive in and try again, or choose somewhere else.',
    }
  }
  if (state === 'switching') {
    return {
      heading: 'Choose a library',
      body:
        'Each library is a separate folder of sessions, clips and reels. ' +
        'Switching does not move anything — the one you leave stays exactly ' +
        'as it is, and you can come back to it.',
    }
  }
  return {
    heading: 'Where should your videos live?',
    body:
      'SplitStep keeps your footage, clips and reels together in one folder. ' +
      'Video is big — a few hours of play fills tens of gigabytes — so most ' +
      'people put this on an external drive and leave their Mac’s disk alone.',
  }
}

/** The suggested path, pre-filled but never applied silently.
 *
 * ~/Movies is the macOS home for video: it is in Finder's sidebar, it is
 * covered by Time Machine, and it reads as obviously the user's. The path is
 * shown with the volume's free space beside it precisely so someone on a
 * 256 GB MacBook sees the problem before 100 GB of footage arrives -- which
 * is why there is a Continue button and not a silent default.
 */
export function defaultLibraryPath(home: string): string {
  return `${home.replace(/\/+$/, '')}/Movies/SplitStep`
}

export function describeTarget(free: number | null, hasLibrary: boolean): string {
  const action = hasLibrary
    ? 'Open the library already in this folder'
    : 'Create a new library in this folder'
  if (free === null) return `${action}.`
  return `${action}. ${formatBytes(free)} free on this volume.`
}

/** Recency order, with unreachable libraries sunk to the bottom.
 *
 * A stable sort, so the config file's most-recent-first order survives
 * within each group. Unreachable entries are kept rather than dropped -- an
 * unplugged drive is the single most common reason to be on this screen, and
 * forgetting it is the one thing the list must not do.
 */
export function sortEntries(entries: LibraryEntry[]): LibraryEntry[] {
  return [...entries].sort((a, b) => Number(b.reachable) - Number(a.reachable))
}
