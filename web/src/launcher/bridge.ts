import type { LibraryEntry } from '../lib/launcher'

/** What the launcher needs from the shell.
 *
 * An interface rather than direct `invoke()` calls, so the page renders in a
 * plain browser during development where there is no Tauri runtime at all.
 * The shell injects the real implementation as a global before the page
 * loads, which also keeps `@tauri-apps/api` out of web/package.json.
 */
export type Bridge = {
  home(): Promise<string>
  known(): Promise<LibraryEntry[]>
  configured(): Promise<string | null>
  /** Native folder picker. Resolves null if the user cancels. */
  pickFolder(): Promise<string | null>
  /** Free bytes on the volume holding `path`, or null if it cannot be read. */
  freeSpace(path: string): Promise<number | null>
  /** Whether `path` already holds a library.db. */
  hasLibrary(path: string): Promise<boolean>
  /** Point of no return: writes config, spawns the sidecar, swaps the window. */
  open(path: string, create: boolean): Promise<void>
}

/** Browser fallback, for `npm run dev:launcher` with no Tauri around it.
 *
 * Deliberately not a mock of a working app: pickFolder returns null and
 * open() throws, because a browser genuinely cannot do either. A fallback
 * that pretended otherwise would hide the one thing worth checking by hand
 * here -- the copy and the layout.
 */
export const browserBridge: Bridge = {
  home: async () => '/Users/you',
  known: async () => [],
  configured: async () => null,
  pickFolder: async () => null,
  freeSpace: async () => null,
  hasLibrary: async () => false,
  open: async () => {
    throw new Error('Opening a library needs the desktop app.')
  },
}

export function resolveBridge(): Bridge {
  const injected = (globalThis as Record<string, unknown>).__SPLITSTEP_BRIDGE__
  return (injected as Bridge) ?? browserBridge
}
