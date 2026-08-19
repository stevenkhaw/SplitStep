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
    el.play().then(
      () => {
        blocked = false
        // Only start polling once playback has actually been granted --
        // arming the loop unconditionally alongside a play() call that may
        // never resolve is what turns a blocked play into a live-lock.
        startLoop()
      },
      (err) => {
        blocked = true
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

  function tick() {
    const el = live()
    if (el) {
      const ms = el.currentTime * 1000
      // Read position on every frame, not via `timeupdate` -- that fires about
      // four times a second and would overshoot each cut by up to 250ms.
      if (onprogress && endMs > startMs) {
        onprogress(Math.min(1, Math.max(0, (ms - startMs) / (endMs - startMs))))
      }
      if (ms >= endMs) {
        // Clear raf before calling out -- onended may synchronously drive a
        // prop update that reruns the effect below, and that shouldn't ever
        // observe a raf id this frame already retired.
        raf = 0
        el.pause()
        onended()
        return
      }
    }
    raf = requestAnimationFrame(tick)
  }

  function startLoop() {
    if (!raf) raf = requestAnimationFrame(tick)
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
    if (src === appliedSrc && startMs === appliedStartMs) return
    appliedSrc = src
    appliedStartMs = startMs

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
        aIsLive = !aIsLive
        return idleEl
      }

      if (!liveEl) return undefined
      if (liveEl.src !== target) liveEl.src = src
      liveEl.currentTime = startMs / 1000
      return liveEl
    })
    if (!target) return

    target.playbackRate = speed
    attemptPlay(target)

    return () => {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
    }
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
</script>

<div class="relative aspect-video w-full overflow-hidden rounded-lg bg-black">
  <video
    bind:this={a}
    class="absolute inset-0 h-full w-full {aIsLive ? 'opacity-100' : 'opacity-0'}"
    playsinline
    muted={false}
  ></video>
  <video
    bind:this={b}
    class="absolute inset-0 h-full w-full {aIsLive ? 'opacity-0' : 'opacity-100'}"
    playsinline
    muted={false}
  ></video>
  {#if blocked}
    <button
      type="button"
      class="absolute inset-0 flex items-center justify-center bg-black/50 text-white"
      onclick={play}
    >
      <span class="rounded-full bg-white px-5 py-2 text-sm font-medium text-black">Click to play</span>
    </button>
  {/if}
</div>
