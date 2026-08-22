import type { ReelItem } from './types'

/**
 * Preview's ordering and advance logic.
 *
 * Deliberately pure: no DOM, no fetch, no timers -- the same rule
 * QueueController follows, and for the same reason (jsdom has no `<video>`).
 * The component is a shell that hands `current`/`next` to VideoDeck and
 * calls `advance()` from its `onended`.
 *
 * Preview plays the PROXY, seeking to each item's span in order, rather
 * than chaining clip files. That reuses VideoDeck exactly as built -- it
 * already takes a source plus in/out points, fires onended at the out-point,
 * and preloads the next span into its second element even across sources.
 * Its limits, stated rather than discovered later: it shows 1080p, and it
 * cannot reveal `-c copy` artifacts (that is what the reel job's duration
 * probe is for). What it does show exactly is TIMING, because reel items
 * carry their own start_ms/end_ms -- so it plays precisely the span the
 * clip contains.
 */
export class ReelPreviewController {
  #items: ReelItem[]
  #index = 0

  constructor(items: ReelItem[]) {
    // Not filtered to clip_ready: preview reads the proxy, so an uncut span
    // previews perfectly well -- and those are exactly the spans a reviewer
    // is deciding whether to cut.
    this.#items = [...items]
  }

  get current(): ReelItem | undefined {
    return this.#items[this.#index]
  }

  get next(): ReelItem | undefined {
    return this.#items[this.#index + 1]
  }

  get index(): number {
    return this.#index
  }

  get total(): number {
    return this.#items.length
  }

  get finished(): boolean {
    return this.#index >= this.#items.length
  }

  advance(): void {
    // Clamped at one past the end rather than allowed to run away: `finished`
    // is a comparison against length, and an unbounded index would keep
    // incrementing on every stray onended a torn-down deck still fires.
    if (this.#index < this.#items.length) this.#index += 1
  }

  restart(): void {
    this.#index = 0
  }

  jumpTo(index: number): void {
    // Silently ignores out of range: the item list can shrink under the
    // preview (a removal in the builder), and seeking to nothing is worse
    // than staying put.
    if (index >= 0 && index < this.#items.length) this.#index = index
  }
}
