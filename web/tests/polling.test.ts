import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startPolling } from '../src/lib/polling'

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true })
}

describe('startPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setHidden(false)
  })

  afterEach(() => {
    vi.useRealTimers()
    setHidden(false)
  })

  it('calls the fetcher immediately on start', async () => {
    const fetcher = vi.fn().mockResolvedValue(undefined)
    const poller = startPolling(fetcher, 3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)
    poller.stop()
  })

  it('calls the fetcher again after the interval elapses', async () => {
    const fetcher = vi.fn().mockResolvedValue(undefined)
    const poller = startPolling(fetcher, 3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(3000)
    expect(fetcher).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(3000)
    expect(fetcher).toHaveBeenCalledTimes(3)

    poller.stop()
  })

  it('stop() cancels the pending timer so no further ticks happen', async () => {
    const fetcher = vi.fn().mockResolvedValue(undefined)
    const poller = startPolling(fetcher, 3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    poller.stop()

    await vi.advanceTimersByTimeAsync(30000)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('stop() during an in-flight fetch prevents the next tick from being scheduled', async () => {
    let resolveFetch!: () => void
    const fetcher = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveFetch = resolve
        }),
    )
    const poller = startPolling(fetcher, 3000)

    // let the initial tick start and begin awaiting the fetch
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    poller.stop()
    resolveFetch()
    await vi.advanceTimersByTimeAsync(0)

    // the in-flight fetch was allowed to resolve, but no next tick was scheduled
    await vi.advanceTimersByTimeAsync(30000)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('does not schedule a timer while the document is hidden', async () => {
    setHidden(true)
    const fetcher = vi.fn().mockResolvedValue(undefined)
    const poller = startPolling(fetcher, 3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1) // the initial tick always runs

    await vi.advanceTimersByTimeAsync(30000)
    expect(fetcher).toHaveBeenCalledTimes(1) // but no follow-up was scheduled

    poller.stop()
  })

  it('resumes immediately on visibilitychange after being hidden', async () => {
    setHidden(true)
    const fetcher = vi.fn().mockResolvedValue(undefined)
    const poller = startPolling(fetcher, 3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(2)

    poller.stop()
  })

  it('a stray visibilitychange while visible does not trigger a duplicate tick', async () => {
    const fetcher = vi.fn().mockResolvedValue(undefined)
    const poller = startPolling(fetcher, 3000)
    await vi.advanceTimersByTimeAsync(0)
    expect(fetcher).toHaveBeenCalledTimes(1)

    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(0)
    // a timer is already pending for the next regular tick; visibilitychange
    // must not fire an extra one on top of it
    expect(fetcher).toHaveBeenCalledTimes(1)

    poller.stop()
  })
})
