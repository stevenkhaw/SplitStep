<script lang="ts">
  import { fractionToMs, msToFraction, nearestHandle } from '../lib/timeline'
  import { clamp } from '../lib/time'
  import type { Rally } from '../lib/types'

  interface Props {
    rally: Rally
    neighbours: Rally[]
    windowStartMs: number
    windowEndMs: number
    /** Fired continuously while dragging a handle -- local-only, must not
     * itself persist to the server (a drag can fire this dozens of times). */
    onchange: (startMs: number, endMs: number) => void
    /** Fired once, when a drag releases -- this is what should be persisted. */
    oncommit: (startMs: number, endMs: number) => void
    onscrub: (ms: number) => void
  }

  let { rally, neighbours, windowStartMs, windowEndMs, onchange, oncommit, onscrub }: Props =
    $props()

  // A dragged boundary can never cross its opposite edge by less than this.
  const MIN_RALLY_MS = 100

  let band = $state<HTMLDivElement>()
  let playheadEl = $state<HTMLDivElement>()
  let dragging = $state<'start' | 'end' | null>(null)

  // The in-progress drag values, tracked here (not read back off the `rally`
  // prop) so the commit fired on release is exactly what this drag produced,
  // independent of whether the parent's prop echo has flushed by then.
  let dragStartMs = 0
  let dragEndMs = 0

  const span = $derived(Math.max(1, windowEndMs - windowStartMs))
  const frac = (ms: number) => msToFraction(ms - windowStartMs, span)

  /**
   * Move the playhead line. Called by the parent on every video-progress
   * tick (up to ~60Hz) -- writing that through Svelte state would re-render
   * this whole band every frame, so it goes straight to the DOM instead,
   * same rule VideoDeck's own progress bar follows.
   */
  export function setPlayheadFraction(fraction: number): void {
    if (playheadEl) playheadEl.style.left = `${clamp(fraction, 0, 1) * 100}%`
  }

  function localFraction(e: PointerEvent): number {
    if (!band) return 0
    const rect = band.getBoundingClientRect()
    return (e.clientX - rect.left) / rect.width
  }

  function onPointerDown(e: PointerEvent) {
    if (!band) return
    const f = localFraction(e)
    const handle = nearestHandle(
      f,
      frac(rally.start_ms),
      frac(rally.end_ms),
      12,
      band.getBoundingClientRect().width,
    )
    if (handle) {
      dragging = handle
      dragStartMs = rally.start_ms
      dragEndMs = rally.end_ms
      band.setPointerCapture(e.pointerId)
    } else {
      onscrub(windowStartMs + fractionToMs(f, span))
    }
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging) return
    const ms = windowStartMs + fractionToMs(localFraction(e), span)
    if (dragging === 'start') {
      dragStartMs = Math.min(ms, dragEndMs - MIN_RALLY_MS)
    } else {
      dragEndMs = Math.max(ms, dragStartMs + MIN_RALLY_MS)
    }
    onchange(dragStartMs, dragEndMs)
  }

  function onPointerUp(e: PointerEvent) {
    if (dragging) oncommit(dragStartMs, dragEndMs)
    dragging = null
    band?.releasePointerCapture(e.pointerId)
  }
</script>

<div
  bind:this={band}
  class="relative h-16 cursor-ew-resize overflow-hidden rounded bg-neutral-800"
  onpointerdown={onPointerDown}
  onpointermove={onPointerMove}
  onpointerup={onPointerUp}
  role="slider"
  tabindex="0"
  aria-label="rally boundaries"
  aria-valuemin={windowStartMs}
  aria-valuemax={windowEndMs}
  aria-valuenow={rally.start_ms}
>
  {#each neighbours as n (n.id)}
    <div
      class="pointer-events-none absolute top-2 bottom-2 rounded bg-blue-500/30"
      style={`left:${frac(n.start_ms) * 100}%;width:${
        Math.max(0.2, frac(n.end_ms) - frac(n.start_ms)) * 100
      }%`}
    ></div>
  {/each}

  <div
    class="pointer-events-none absolute top-1.5 bottom-1.5 rounded border-2 border-blue-400 bg-blue-400/25"
    style={`left:${frac(rally.start_ms) * 100}%;width:${
      Math.max(0.4, frac(rally.end_ms) - frac(rally.start_ms)) * 100
    }%`}
  >
    <div class="absolute -top-0.5 -bottom-0.5 -left-1 w-2 rounded bg-blue-400"></div>
    <div class="absolute -top-0.5 -bottom-0.5 -right-1 w-2 rounded bg-blue-400"></div>
  </div>

  <div
    bind:this={playheadEl}
    class="pointer-events-none absolute inset-y-0 w-0.5 bg-red-500"
    style={`left:${frac(rally.start_ms) * 100}%`}
  ></div>
</div>
