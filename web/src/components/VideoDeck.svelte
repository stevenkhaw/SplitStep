<script lang="ts">
  interface Props {
    src: string
    startMs: number
    endMs: number
    nextSrc?: string
    nextStartMs?: number
    speed?: number
    onended: () => void
    onprogress?: (fraction: number) => void
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
  }: Props = $props()

  // Two elements, swapped on advance. While A plays rally N, B is already
  // seeked and buffered at rally N+1, so advancing costs no stall. A single
  // element re-seeking would stall ~400ms every time, and gaps between
  // rallies can be a minute or more.
  let a: HTMLVideoElement
  let b: HTMLVideoElement
  let aIsLive = $state(true)
  let raf = 0

  const live = () => (aIsLive ? a : b)
  const idle = () => (aIsLive ? b : a)

  export function currentMs(): number {
    return live() ? live().currentTime * 1000 : 0
  }

  export function seekTo(ms: number): void {
    if (live()) live().currentTime = ms / 1000
  }

  export function play(): void {
    live()?.play()
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
        el.pause()
        onended()
        raf = 0
        return
      }
    }
    raf = requestAnimationFrame(tick)
  }

  function startLoop() {
    if (!raf) raf = requestAnimationFrame(tick)
  }

  $effect(() => {
    // (re)arm whenever the current rally changes
    src
    startMs
    const el = live()
    if (!el) return
    if (el.src !== new URL(src, window.location.href).href) el.src = src
    el.currentTime = startMs / 1000
    el.playbackRate = speed
    el.play().catch(() => {})
    startLoop()
    return () => {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
    }
  })

  $effect(() => {
    // preload the next rally into the idle element
    const el = idle()
    if (!el || !nextSrc || nextStartMs === undefined) return
    if (el.src !== new URL(nextSrc, window.location.href).href) el.src = nextSrc
    el.currentTime = nextStartMs / 1000
    el.pause()
  })

  $effect(() => {
    const el = live()
    if (el) el.playbackRate = speed
  })

  export function swap(): void {
    aIsLive = !aIsLive
  }
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
</div>
