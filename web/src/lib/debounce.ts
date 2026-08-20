/** A debounced function, plus a way to drop whatever call is pending. */
export interface Debounced<Args extends unknown[]> {
  (...args: Args): void
  cancel: () => void
}

/**
 * Wraps `fn` so a burst of calls collapses into a single call to `fn` with
 * the arguments of the *last* call, `waitMs` after the burst goes quiet.
 * Trailing-edge only -- a slider mid-drag must not invoke `fn` at all until
 * the user pauses, which is the whole point of debouncing an endpoint that
 * costs real time per call (see api.scores, ~83ms at one-hour scale).
 */
export function debounce<Args extends unknown[]>(
  fn: (...args: Args) => void,
  waitMs: number,
): Debounced<Args> {
  let timer: ReturnType<typeof setTimeout> | undefined

  function debounced(...args: Args): void {
    if (timer !== undefined) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = undefined
      fn(...args)
    }, waitMs)
  }

  debounced.cancel = () => {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }

  return debounced
}
