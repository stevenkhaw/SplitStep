<script lang="ts">
  import { MIN_RALLY_MS, fractionToMs, msToFraction, nearestHandle } from '../lib/timeline'
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
    /** Fired the instant a handle-drag begins, before any pointermove --
     * this is the parent's cue to freeze whatever coordinate space
     * windowStartMs/windowEndMs represent for the duration of the drag (see
     * TimelineMode's frozen window, Finding 3: without this, the window
     * recenters under the pointer mid-drag and each move amplifies the
     * last). Not fired for a scrub click, which is a single discrete jump,
     * not a drag. */
    ondragstart?: () => void
    /** Fired once a drag ends -- on a normal release (after oncommit) or on
     * a pointercancel (no oncommit). The parent's cue to release whatever
     * it froze in ondragstart. */
    ondragend?: () => void
  }

  let {
    rally,
    neighbours,
    windowStartMs,
    windowEndMs,
    onchange,
    oncommit,
    onscrub,
    ondragstart,
    ondragend,
  }: Props = $props()

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
      ondragstart?.()
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

  // Shared by a normal release and a cancelled drag -- both end the drag the
  // same way locally (clear `dragging`, release capture, tell the parent to
  // stop freezing); only a normal release also commits.
  function endDrag(e: PointerEvent) {
    const wasDragging = dragging !== null
    dragging = null
    band?.releasePointerCapture(e.pointerId)
    if (wasDragging) ondragend?.()
  }

  function onPointerUp(e: PointerEvent) {
    if (dragging) oncommit(dragStartMs, dragEndMs)
    endDrag(e)
  }

  // Without this, a cancelled drag (e.g. the OS interrupts with its own
  // gesture, or the pointer leaves the window) leaves `dragging` set, so the
  // next hover over the band keeps moving the handle with no button held.
  function onPointerCancel(e: PointerEvent) {
    endDrag(e)
  }
</script>

<div
  bind:this={band}
  class="relative h-16 cursor-ew-resize overflow-hidden rounded bg-neutral-800"
  onpointerdown={onPointerDown}
  onpointermove={onPointerMove}
  onpointerup={onPointerUp}
  onpointercancel={onPointerCancel}
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

  <!--
    No reactive `style` binding here, deliberately (Finding 3): this
    element's position is written imperatively-only via setPlayheadFraction,
    called up to ~60Hz from VideoDeck's onprogress. A `style={...}`
    expression derived from `rally`/the window props would re-render (and
    silently clobber) that write every time either changes -- which,
    mid-drag, is every pointermove. The static string below is the initial
    position only, overwritten before first paint by TimelineMode's mount
    effect.
  -->
  <div
    bind:this={playheadEl}
    class="pointer-events-none absolute inset-y-0 w-0.5 bg-red-500"
    style="left: 0%"
  ></div>
</div>
