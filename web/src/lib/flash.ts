import type { QueueAction } from './queue'

/**
 * The brief confirmation shown over the video after a verdict.
 *
 * It exists because `X` advances to the next rally the instant it lands, so
 * a rejection was visually indistinguishable from pressing the right arrow:
 * the video changed either way and nothing said which had happened. Star and
 * point had a quieter version of the same problem -- the toggle fills, but
 * the eye is on the video, not on the status line under it.
 *
 * All three are toggles, so the label names the direction. "Starred" on a
 * keypress that un-starred would be a worse lie than showing nothing.
 */
export type FlashTone = 'star' | 'point' | 'reject' | 'neutral'

export interface VerdictFlash {
  label: string
  glyph: string
  tone: FlashTone
}

export function flashFor(action: QueueAction): VerdictFlash | null {
  switch (action.kind) {
    case 'star':
      return {
        label: action.starred ? 'Starred' : 'Unstarred',
        glyph: '★',
        tone: 'star',
      }
    case 'point':
      return {
        label: action.point ? 'Point' : 'Not a point',
        glyph: '●',
        tone: 'point',
      }
    case 'winner':
      // The panel beside the video names the player; the flash over the
      // footage keys on the letter the reviewer just pressed. QueueMode
      // overrides this label with the player's name (see Task 7) -- this
      // default is what tests see.
      return {
        label: `Point · ${action.winner === 'a' ? 'A' : 'B'}`,
        glyph: '●',
        tone: 'point',
      }
    case 'reject':
      // 'reject', not 'danger'. Same rule the palette follows: detection is
      // recall-biased, rejecting is the most frequent action in the app, and
      // dressing the routine case as a failure states the wrong thing about
      // ordinary work.
      return {
        label: action.rejected ? 'Rejected' : 'Kept',
        glyph: '✕',
        tone: 'reject',
      }
    case 'skip':
      // Walking the pass is movement, not a judgement. Flashing here would
      // fire on nearly every keypress and drown the confirmations that mean
      // something.
      return null
    case 'undo':
      // UndoAction carries the restored flags but not which of the three it
      // reverted, so the label deliberately does not guess.
      return { label: 'Undone', glyph: '⟲', tone: 'neutral' }
  }
}
