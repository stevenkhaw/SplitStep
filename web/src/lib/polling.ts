export interface Poller {
  /** Cancels the pending timer (if any) and removes the visibility listener.
   * A fetch already in flight is allowed to finish, but its result is
   * discarded and no further tick is scheduled. Safe to call more than once. */
  stop: () => void
}

/**
 * Calls `fetcher` immediately, then every `intervalMs` for as long as the
 * document is visible. While `document.hidden` is true no timer is armed;
 * becoming visible again (via the `visibilitychange` event) triggers an
 * immediate tick rather than waiting out the rest of the interval.
 *
 * `fetcher` is expected to handle its own errors -- a rejection is swallowed
 * here too, purely as a safety net, so one bad response can't kill the loop.
 */
export function startPolling(fetcher: () => Promise<void>, intervalMs: number): Poller {
  let cancelled = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let inFlight = false

  const scheduleNext = () => {
    if (cancelled || document.hidden) return
    timer = setTimeout(tick, intervalMs)
  }

  const tick = async () => {
    if (cancelled || inFlight) return
    inFlight = true
    timer = undefined
    try {
      await fetcher()
    } catch {
      // fetcher should catch its own errors; this is only a backstop so an
      // unexpected rejection doesn't stall the loop.
    } finally {
      inFlight = false
    }
    scheduleNext()
  }

  const onVisibilityChange = () => {
    if (!cancelled && !document.hidden && timer === undefined && !inFlight) {
      tick()
    }
  }

  document.addEventListener('visibilitychange', onVisibilityChange)
  tick()

  return {
    stop() {
      cancelled = true
      if (timer !== undefined) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    },
  }
}
