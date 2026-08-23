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
   * How far from a handle a press still counts as grabbing it.
   *
   * Deliberately larger than the handle's 12px visual width -- ordinary hit
   * slop, and the failure mode here is worse than a plain miss: a press that
   * matches no handle falls through to `onscrub`, so missing the grab moves
   * the playhead and reads as "the drag didn't take" rather than as nothing
   * happening. 24px is the WCAG 2.5.8 minimum target size.
   *
   * Bounded above by the rallies themselves: the shortest rally in a real
   * session (2.4 s) draws ~66px wide in the 40 s zoom window, so two 24px
   * zones still leave a gap to scrub in. If the two ever do meet on a
   * narrower rally, `nearestHandle` splits the difference at the midpoint
   * rather than favouring one side, so it degrades gracefully instead of
   * making one handle unreachable.
   */
  const HANDLE_GRAB_PX = 24

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
      HANDLE_GRAB_PX,
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
  class="relative h-16 cursor-ew-resize overflow-hidden rounded bg-surface-2"
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
      class="pointer-events-none absolute top-2 bottom-2 rounded bg-accent/30"
      style={`left:${frac(n.start_ms) * 100}%;width:${
        Math.max(0.2, (frac(n.end_ms) - frac(n.start_ms)) * 100)
      }%`}
    ></div>
  {/each}

  <!--
    The floors below are PERCENTAGES, so the `* 100` belongs inside the
    Math.max, not outside it. Outside, `Math.max(0.4, fraction)` compares a
    0..1 fraction against 0.4 and reads as a 40% minimum -- which, against
    TimelineMode's 40 s zoom window, silently inflated every rally shorter
    than 16 s to a fixed 16 s wide. Median rally here is 7.6 s.

    That is not merely cosmetic: `nearestHandle` hit-tests the pointer
    against frac(start_ms)/frac(end_ms), while the user aims at the box's
    drawn edges. Inflating the width moved the drawn right handle ~21% of the
    band away from where the hit test looked for it, far outside the 12px
    grab radius, so grabbing it fell through to a scrub and the right handle
    could not be dragged at all. The left edge was unaffected, since `left`
    was always the true fraction -- which is why only the right handle broke.
  -->
  <div
    class="pointer-events-none absolute top-1.5 bottom-1.5 rounded border-2 border-accent bg-accent/25"
    style={`left:${frac(rally.start_ms) * 100}%;width:${
      Math.max(0.4, (frac(rally.end_ms) - frac(rally.start_ms)) * 100)
    }%`}
  >
    <!-- 12px wide, centred on the boundary via the -1.5 inset. The press
         target is wider still (HANDLE_GRAB_PX); this is only the affordance,
         sized so the thing you aim at is actually visible against a 66px-wide
         short rally. -->
    <div class="absolute -top-0.5 -bottom-0.5 -left-1.5 w-3 rounded bg-accent"></div>
    <div class="absolute -top-0.5 -bottom-0.5 -right-1.5 w-3 rounded bg-accent"></div>
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
    class="pointer-events-none absolute inset-y-0 w-0.5 bg-fg"
    style="left: 0%"
  ></div>
</div>
