<script lang="ts">
  import { untrack } from 'svelte'

  interface Props {
    src: string
    startMs: number
    endMs: number
    nextSrc?: string
    nextStartMs?: number
    speed?: number
    onended: () => void
    onprogress?: (fraction: number) => void
    onblocked?: (err: unknown) => void
  }

  let {
    src,
    startMs,
    endMs,
    nextSrc,
    nextStartMs,
    speed = 1,
    onended,
    onprogress,
    onblocked,
  }: Props = $props()

  // Two elements, swapped on advance. While A plays rally N, B is already
  // seeked and buffered at rally N+1, so advancing costs no stall. A single
  // element re-seeking would stall ~400ms every time, and gaps between
  // rallies can be a minute or more.
  let a: HTMLVideoElement
  let b: HTMLVideoElement
  let aIsLive = $state(true)
  let raf = 0

  // Unmuted autoplay is blocked by browsers until the page has real user
  // activation. Rather than swallow that rejection, track it so the template
  // can offer a click-to-play affordance -- the click itself is a user
  // gesture, so the retry succeeds.
  let blocked = $state(false)

  // A genuine media load failure (e.g. a missing/expired proxy returning
  // 404). Deliberately separate from `blocked`: telling the user to "click
  // to play" when playback can never succeed is actively misleading, so this
  // gets its own honest message instead of borrowing the autoplay scrim.
  let loadError = $state<string | null>(null)

  // How close a preloaded element's currentTime has to be to a requested
  // startMs to count as "already positioned there" -- seeking is not exact.
  const SEEK_TOLERANCE_MS = 100

  const live = () => (aIsLive ? a : b)
  const idle = () => (aIsLive ? b : a)

  function resolvedUrl(u: string): string {
    return new URL(u, window.location.href).href
  }

  export function currentMs(): number {
    return live() ? live().currentTime * 1000 : 0
  }

  export function seekTo(ms: number): void {
    if (live()) live().currentTime = ms / 1000
  }

  function attemptPlay(el: HTMLVideoElement): void {
    // Starting playback is a new pass over the rally, so the out-point has to
    // be detectable again. `rallyFinished` used to be cleared only where the
    // rally itself changed, which `replay()` does not do -- it seeks the same
    // src/startMs back to the beginning. A looping clip therefore played
    // through once, and on the second pass finishRally() returned at its
    // guard before pausing, so playback ran on into the following footage
    // with the rAF loop already torn down and the timeupdate backstop hitting
    // the same guard. Every restart funnels through here: replay(), play(),
    // the click-to-play scrim retry, and the rally-change effect.
    rallyFinished = false
    el.play().then(
      () => {
        blocked = false
        // Only start polling once playback has actually been granted --
        // arming the loop unconditionally alongside a play() call that may
        // never resolve is what turns a blocked play into a live-lock.
        startLoop()
      },
      (err) => {
        // Only a genuine autoplay rejection should raise the click-to-play
        // scrim. `play()` also rejects with AbortError whenever a pending
        // call is interrupted by a new load/seek -- e.g. every pointermove
        // while dragging the start handle in timeline mode -- and treating
        // that as a block flashes the scrim over the video mid-drag.
        if (err?.name === 'NotAllowedError') blocked = true
        onblocked?.(err)
      },
    )
  }

  export function play(): void {
    const el = live()
    if (el) attemptPlay(el)
  }

  export function pause(): void {
    live()?.pause()
  }

  export function paused(): boolean {
    return live()?.paused ?? true
  }

  export function replay(): void {
    seekTo(startMs)
    play()
  }

  // Whether the current rally has already been ended. rAF and `timeupdate`
  // both race to detect the out-point; this guard is what lets either win
  // without double-firing onended. Reset to false each time a rally arms.
  let rallyFinished = false

  function finishRally(el: HTMLVideoElement): void {
    if (rallyFinished) return
    rallyFinished = true
    // The rAF loop and the `timeupdate` backstop race to get here; whichever
    // loses still has a frame scheduled. Cancel it, don't just zero the
    // handle -- an uncancelled frame is a leaked, untracked loop that the
    // next rally's teardown can no longer reach (it only knows about the
    // handle it itself armed), and it will keep re-arming itself against
    // whatever element is live by the time it fires.
    if (raf) cancelAnimationFrame(raf)
    raf = 0
    el.pause()
    onended()
  }

  // Shared out-point check used by both backstop paths (timeupdate and the
  // visibility-restore check) -- NOT by the rAF loop, which inlines its own
  // check below because it also needs `ms` for onprogress.
  function checkBoundary(el: HTMLVideoElement): void {
    if (el.currentTime * 1000 >= endMs) finishRally(el)
  }

  function tick() {
    const el = live()
    if (el) {
      const ms = el.currentTime * 1000
      // Read position on every frame, not via `timeupdate` -- that fires about
      // four times a second and would overshoot each cut by up to 250ms. This
      // stays the precise path; `timeupdate` below is only a backstop for
      // when rAF itself gets throttled (e.g. a hidden/backgrounded tab), so
      // the out-point can still be missed for the several hundred ms rAF is
      // silent, but never indefinitely.
      if (onprogress && endMs > startMs) {
        onprogress(Math.min(1, Math.max(0, (ms - startMs) / (endMs - startMs))))
      }
      if (ms >= endMs) {
        finishRally(el)
        return
      }
    }
    raf = requestAnimationFrame(tick)
  }

  function startLoop() {
    if (!raf) raf = requestAnimationFrame(tick)
  }

  // Arms (or re-arms) the rAF loop and the `timeupdate` backstop for
  // `target`, returning the cleanup that tears both down. This is the only
  // place either gets started, and every path that tears them down (the
  // effect cleanup below, on any rerun) must go back through here rather
  // than returning early -- a teardown that isn't paired with a re-arm
  // leaves the out-point undetected for the rest of the rally.
  function armLoop(target: HTMLVideoElement): () => void {
    startLoop()
    const handleTimeUpdate = () => checkBoundary(target)
    target.addEventListener('timeupdate', handleTimeUpdate)
    return () => {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
      target.removeEventListener('timeupdate', handleTimeUpdate)
    }
  }

  // The last (src, startMs) this effect actually applied. Without this, any
  // unrelated rerun would redo the seek and restart the loop it just armed --
  // in particular, the aIsLive write below would otherwise do exactly that,
  // since live()/idle() read aIsLive.
  let appliedSrc: string | undefined
  let appliedStartMs: number | undefined

  $effect(() => {
    // (re)arm whenever the current rally changes
    src
    startMs
    if (src === appliedSrc && startMs === appliedStartMs) {
      // Nothing about the rally changed -- some other tracked read (e.g.
      // `speed`, historically, before it was wrapped in untrack below) is
      // what forced this rerun. Svelte still ran the *previous* cleanup
      // before getting here, which cancelled the rAF loop and dropped the
      // `timeupdate` listener, so this path must re-arm against the
      // still-current rally rather than return and strand it torn down.
      const target = untrack(() => live())
      if (!target) return
      return armLoop(target)
    }
    appliedSrc = src
    appliedStartMs = startMs
    loadError = null

    // Reading aIsLive (via live()/idle()) and writing it below must not
    // become a tracked dependency of *this* effect: live()/idle() read it
    // internally, and if a swap here re-triggered this same effect, its
    // cleanup would cancel the frame it just armed. untrack() keeps the
    // swap an ordinary side effect instead of a self-retrigger.
    //
    // This also depends on this effect running before the preload effect
    // below within the same flush (verified: Svelte runs same-level effects
    // in declaration order), so the idle element still holds the *previous*
    // preload -- exactly the rally now becoming current -- when we check it
    // here, before the preload effect points it at the *next* next rally.
    // Keep this effect declared first.
    const target = untrack(() => {
      const idleEl = idle()
      const liveEl = live()
      const target = resolvedUrl(src)
      const hitPreload =
        !!idleEl && idleEl.src === target && Math.abs(idleEl.currentTime * 1000 - startMs) < SEEK_TOLERANCE_MS

      if (hitPreload) {
        // The idle element is already this rally, buffered and seeked --
        // swap to it instead of re-seeking the live one. No stall: this is
        // the entire reason this component owns two elements.
        //
        // Silence the element being swapped away from, here rather than
        // relying on the preload effect below to do it. That effect pauses
        // the newly-idle element only as a side effect of loading the rally
        // after next into it, and it returns early when there is no next
        // rally -- so advancing onto the LAST rally of a pass used to orphan
        // this element still playing. Audio kept running under the new clip
        // and nothing could stop it, since pause() only ever reaches live().
        liveEl?.pause()
        aIsLive = !aIsLive
        return idleEl
      }

      if (!liveEl) return undefined
      if (liveEl.src !== target) liveEl.src = src
      liveEl.currentTime = startMs / 1000
      return liveEl
    })
    if (!target) return

    // Redundant with the dedicated playbackRate-sync effect below, and must
    // stay untracked: a tracked read of `speed` here makes every speed
    // change invalidate this effect, running the destructive cleanup above
    // and then hitting the guard -- which is exactly the trap this effect's
    // re-arm path now exists to catch, but the fix starts with not reading
    // this at all.
    target.playbackRate = untrack(() => speed)
    // rallyFinished is cleared inside attemptPlay, which owns it for every
    // restart path -- resetting it again here would just be a second place to
    // keep in sync.
    attemptPlay(target)

    // Backstop: if rAF gets throttled (hidden/backgrounded tab), `tick()`
    // may not run for a long time, and the out-point would otherwise be
    // missed entirely -- playback would run straight through into whatever
    // footage follows. `timeupdate` fires ~4x/sec regardless of rAF
    // throttling, so it catches the boundary late (up to ~250ms) but it
    // *does* catch it. finishRally()'s guard keeps this from double-firing
    // against the rAF path.
    return armLoop(target)
  })

  $effect(() => {
    // preload the next rally into the idle element
    const el = idle()
    if (!el || !nextSrc || nextStartMs === undefined) return
    if (el.src !== resolvedUrl(nextSrc)) el.src = nextSrc
    el.currentTime = nextStartMs / 1000
    el.pause()
  })

  $effect(() => {
    const el = live()
    if (el) el.playbackRate = speed
  })

  // A tab restored from hidden/throttled should snap to the correct state
  // immediately rather than waiting for the next `timeupdate` (which may
  // itself be delayed if the tab was fully suspended). Deliberately does
  // NOT pause on hide -- a rally may still be worth listening to while the
  // user reads something else, and audio is intentionally on.
  $effect(() => {
    function handleVisibilityChange() {
      if (document.visibilityState !== 'visible') return
      const el = live()
      if (el) checkBoundary(el)
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  })

  // A missing/expired proxy (404), a decode failure, or any other real media
  // error is not an autoplay block -- surface it distinctly and honestly
  // instead of leaving a black box behind a "Click to play" button that can
  // never succeed.
  function handleError(el: HTMLVideoElement): void {
    const code = el.error?.code
    loadError =
      code === MediaError.MEDIA_ERR_NETWORK
        ? 'Video failed to load: network error.'
        : code === MediaError.MEDIA_ERR_DECODE
          ? 'Video failed to load: could not decode.'
          : code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
            ? 'Video failed to load: not found or unsupported.'
            : 'Video failed to load.'
  }
</script>

<div class="relative aspect-video w-full overflow-hidden rounded-lg bg-black">
  <video
    bind:this={a}
    class="absolute inset-0 h-full w-full {aIsLive ? 'opacity-100' : 'opacity-0'}"
    playsinline
    muted={false}
    onerror={() => handleError(a)}
  ></video>
  <video
    bind:this={b}
    class="absolute inset-0 h-full w-full {aIsLive ? 'opacity-0' : 'opacity-100'}"
    playsinline
    muted={false}
    onerror={() => handleError(b)}
  ></video>
  {#if loadError}
    <div class="absolute inset-0 flex items-center justify-center bg-black/50 text-fg">
      <span class="rounded-full bg-fg px-5 py-2 text-body font-medium text-bg">{loadError}</span>
    </div>
  {:else if blocked}
    <button
      type="button"
      class="absolute inset-0 flex items-center justify-center bg-black/50 text-fg"
      onclick={play}
    >
      <span class="rounded-full bg-fg px-5 py-2 text-body font-medium text-bg">Click to play</span>
    </button>
  {/if}
</div>
