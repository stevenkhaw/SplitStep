import { isEditableTarget } from './keyboard'
import type { AppMode } from './types'

/**
 * The keyboard reference, as data.
 *
 * This exists because the legend under the video was a hand-written sentence
 * listing eleven bindings, at the same visual weight as the live data above
 * it, and nothing connected it to the handlers it described -- a binding
 * could move and the sentence would keep claiming the old one. Both the
 * inline strip and the `?` overlay now render from these tables, and
 * tests/shortcuts.test.ts checks the strip is a subset of the overlay and
 * that no key is bound twice within a mode.
 *
 * Keys are written the way they are printed on a key, not the way
 * KeyboardEvent.key reports them: `S`, not `s`/`S`.
 */
export type ShortcutMode = 'queue' | 'timeline' | 'label' | 'audit'

export const MODES: ShortcutMode[] = ['queue', 'timeline', 'label', 'audit']

export interface Shortcut {
  /** One entry per key that does the same thing, e.g. the four speed keys. */
  keys: string[]
  label: string
  /** Bindings friend mode hides (the tuning tools). The strip and the `?`
   *  overlay render from this flag; the behavioral gate is separate and
   *  hand-written (Session.openLabel returns early in friend mode), so a
   *  new devOnly binding needs BOTH: tag it here so the reference stops
   *  advertising it, and gate its handler so the keypress goes dead. */
  devOnly?: true
}

export interface ShortcutGroup {
  title: string
  items: Shortcut[]
}

const QUEUE: ShortcutGroup[] = [
  {
    title: 'Review',
    items: [
      { keys: ['S'], label: 'Star this rally' },
      { keys: ['P'], label: 'Mark it a point' },
      { keys: ['A', 'B'], label: 'Who won the point, when tracking a score' },
      { keys: ['X'], label: 'Reject it (again to undo)' },
      { keys: ['U'], label: 'Undo the last verdict' },
      { keys: ['N'], label: 'Write a note' },
    ],
  },
  {
    title: 'Playback',
    items: [
      { keys: ['Space'], label: 'Play or pause' },
      { keys: ['R'], label: 'Replay from the start' },
      { keys: ['`', '1', '2', '3'], label: 'Playback speed' },
    ],
  },
  {
    title: 'Move',
    items: [
      { keys: ['←'], label: 'Previous rally' },
      { keys: ['→'], label: 'Next rally' },
    ],
  },
  {
    title: 'Open',
    items: [
      { keys: ['T'], label: 'Timeline, to fix the boundaries' },
      { keys: ['L'], label: 'Label mode, to judge the detector', devOnly: true },
      { keys: ['H'], label: 'Show rejected rallies, to bring one back' },
      { keys: ['?'], label: 'This list' },
    ],
  },
]

const TIMELINE: ShortcutGroup[] = [
  {
    title: 'Trim',
    items: [
      { keys: ['['], label: 'Set the in point here' },
      { keys: [']'], label: 'Set the out point here' },
    ],
  },
  {
    // `C` rather than `S` for "split": S is *star* in queue mode, the
    // reviewer crosses between the two modes constantly, and a reflex S in
    // timeline that cut instead of starred is the worst possible misfire for
    // a key whose inverse is conditional.
    title: 'Split',
    items: [
      { keys: ['C'], label: 'Split here into two rallies' },
      { keys: ['U'], label: 'Merge back into the previous' },
    ],
  },
  {
    title: 'Playback',
    items: [
      { keys: ['Space'], label: 'Play or pause' },
      { keys: [','], label: 'Back one frame' },
      { keys: ['.'], label: 'Forward one frame' },
    ],
  },
  {
    title: 'Leave',
    items: [
      { keys: ['Esc'], label: 'Back to the queue' },
      { keys: ['?'], label: 'This list' },
    ],
  },
]

const LABEL: ShortcutGroup[] = [
  {
    title: 'Verdict',
    items: [
      { keys: ['1'], label: 'Clean rally' },
      { keys: ['2'], label: 'Not play' },
      { keys: ['3'], label: 'Partly play' },
      { keys: ['4'], label: 'Unsure' },
    ],
  },
  {
    // Left-hand keys are the clip's start and right-hand its end; the first
    // of each pair is early and the second late. That is the layout the
    // bindings were chosen for, so the reference keeps them in that order.
    title: 'Boundary',
    items: [
      { keys: ['Q'], label: 'Starts early' },
      { keys: ['W'], label: 'Starts late' },
      { keys: ['O'], label: 'Ends early' },
      { keys: ['P'], label: 'Ends late' },
    ],
  },
  {
    title: 'Playback',
    items: [
      { keys: ['Space'], label: 'Play or pause' },
      { keys: ['R'], label: 'Replay from the start' },
    ],
  },
  {
    title: 'Move',
    items: [
      { keys: ['←'], label: 'Previous rally' },
      { keys: ['→'], label: 'Next rally' },
      { keys: ['U'], label: 'Retract this judgement' },
    ],
  },
  {
    title: 'Leave',
    items: [
      { keys: ['L', 'Esc'], label: 'Back to the queue' },
      { keys: ['?'], label: 'This list' },
    ],
  },
]

// The blind pass (#/audit/<source>). Verdict keys are LABEL's, deliberately
// -- the two modes ask the same question about a span, and a reviewer moving
// between them must not have to remember which `2` means what. What is
// absent is the boundary row: a sampled window's edges came from a seeded
// tiling, so there is no detector edge here to call early or late.
const AUDIT: ShortcutGroup[] = [
  {
    title: 'Verdict',
    items: [
      { keys: ['1'], label: 'Clean rally' },
      { keys: ['2'], label: 'Not play' },
      { keys: ['3'], label: 'Partly play' },
      { keys: ['4'], label: 'Unsure' },
    ],
  },
  {
    title: 'Playback',
    items: [
      { keys: ['Space'], label: 'Play or pause' },
      { keys: ['R'], label: 'Replay from the start' },
    ],
  },
  {
    title: 'Move',
    items: [
      { keys: ['←'], label: 'Previous window' },
      { keys: ['→'], label: 'Next window' },
      { keys: ['U'], label: 'Retract this judgement' },
    ],
  },
  {
    title: 'Leave',
    items: [
      { keys: ['Esc'], label: 'Back to the session' },
      { keys: ['?'], label: 'This list' },
    ],
  },
]

const BY_MODE: Record<ShortcutMode, ShortcutGroup[]> = {
  queue: QUEUE,
  timeline: TIMELINE,
  label: LABEL,
  audit: AUDIT,
}

/** Friend mode drops the devOnly entries and any group that leaves empty.
 *  Item objects pass through by reference on purpose: the strip-subset test
 *  (and the strip itself) compare by identity. */
function visible(groups: ShortcutGroup[], appMode: AppMode): ShortcutGroup[] {
  if (appMode === 'dev') return groups
  return groups
    .map((g) => ({ ...g, items: g.items.filter((s) => !s.devOnly) }))
    .filter((g) => g.items.length > 0)
}

// appMode is required, not defaulted: a default of 'dev' would fail open --
// a future surface rendering shortcuts could compile clean while advertising
// the tuning keys to friend mode. The compiler forcing every caller to make
// the friend/dev decision is the cheapest gate there is.
export function shortcutGroups(mode: ShortcutMode, appMode: AppMode): ShortcutGroup[] {
  return visible(BY_MODE[mode], appMode)
}

/** Every key a mode binds, flattened -- what the duplicate check reads.
 *  Always the full dev set: a key hidden in friend mode still conflicts if
 *  bound twice. */
export function shortcutKeys(mode: ShortcutMode): string[] {
  return shortcutGroups(mode, 'dev').flatMap((g) => g.items.flatMap((s) => s.keys))
}

// Indices into the groups above rather than copies, so the inline strip is
// literally the same objects the overlay renders and cannot say something
// different. Six is the ceiling: past that the strip wraps and stops being
// glanceable, which is the failure it replaces.
const PRIMARY: Record<ShortcutMode, Shortcut[]> = {
  queue: [QUEUE[0].items[0], QUEUE[0].items[1], QUEUE[0].items[3], QUEUE[0].items[4],
          QUEUE[2].items[1], QUEUE[3].items[3]],
  timeline: [TIMELINE[0].items[0], TIMELINE[0].items[1], TIMELINE[1].items[0],
             TIMELINE[1].items[1], TIMELINE[3].items[0], TIMELINE[3].items[1]],
  label: [LABEL[0].items[0], LABEL[0].items[1], LABEL[3].items[1], LABEL[3].items[2],
          LABEL[4].items[0], LABEL[4].items[1]],
  audit: [AUDIT[0].items[0], AUDIT[0].items[1], AUDIT[0].items[2], AUDIT[2].items[2],
          AUDIT[3].items[0], AUDIT[1].items[0]],
}

/** The handful that stay visible under the video. */
export function primaryShortcuts(mode: ShortcutMode, appMode: AppMode): Shortcut[] {
  return PRIMARY[mode].filter((s) => appMode === 'dev' || !s.devOnly)
}

/**
 * Whether this event should open the reference.
 *
 * `/` counts as well as `?`: the shifted-slash position is a US-layout
 * assumption, and the unshifted key is unbound everywhere in the app. The
 * editable-target and modifier guards are the same two every mode handler
 * already applies -- a `?` typed into a note or a preset name is text.
 */
export function isHelpKey(e: KeyboardEvent): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return false
  if (isEditableTarget(e.target)) return false
  return e.key === '?' || e.key === '/'
}
