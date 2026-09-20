<script lang="ts">
  import { clamp } from '../lib/time'
  import { msToFraction, scrubMsAt, scrubXFor } from '../lib/timeline'
  import type { DraftSpan } from '../lib/timeline'
  import type { Rally } from '../lib/types'

  interface Props {
    /** The whole file, not a window into it -- this bar exists because
     * VideoDeck can only ever play between an in and an out point, so there
     * was no way to reach footage the detector never proposed a rally for. */
    durationMs: number
    /** The span being drawn. Never null: `N` seeds one (see draftSpanAt). */
    draft: DraftSpan
    /** This source's existing rallies, drawn faintly. The reviewer is
     * hunting for play that isn't here yet, so where the detector already
     * found some is exactly the context that makes a gap worth watching. */
    rallies: Rally[]
    onscrub: (ms: number) => void
  }

  let { durationMs, draft, rallies, onscrub }: Props = $props()

  let bar = $state<HTMLDivElement>()
  let playheadEl = $state<HTMLDivElement>()
  // Measured, because everything below is drawn in pixels rather than
  // percentages (see the floor comment on the draft box). `bind:clientWidth`
  // keeps it correct across a window resize, which a width read once on
  // mount would not.
  let widthPx = $state(0)

  /**
   * A whole hour under a ~900px bar is ~4 seconds per pixel, so a rally --
   * and the seeded 100ms draft especially -- rounds to nothing at all. The
   * floor is in PIXELS and applied after the conversion, which is why the
   * geometry here is `scrubXFor` rather than a percentage: a percentage
   * floor means a different number of pixels on every window width, and
   * ZoomBand has already paid for that mistake once (its floors are
   * percentages and were briefly compared against fractions, inflating
   * every short rally to a fixed 16 s wide).
   */
  const MIN_DRAW_PX = 3

  function xFor(ms: number): number {
    return scrubXFor(ms, widthPx, durationMs)
  }

  function widthFor(startMs: number, endMs: number, floorPx: number): number {
    return Math.max(floorPx, xFor(endMs) - xFor(startMs))
  }

  /**
   * Same imperative write ZoomBand's playhead uses, for the same reason:
   * VideoDeck's onprogress fires at up to ~60Hz and routing that through
   * Svelte state would re-render this bar every frame.
   */
  export function setPlayheadFraction(fraction: number): void {
    if (playheadEl) playheadEl.style.left = `${clamp(fraction, 0, 1) * 100}%`
  }

  function onPointerDown(e: PointerEvent) {
    if (!bar) return
    const rect = bar.getBoundingClientRect()
    // No handle hit-test, unlike ZoomBand: at four seconds to the pixel a
    // dragged end would be a worse instrument than `[` and `]`, which put
    // the boundary on the frame the reviewer is actually looking at. A
    // press here only moves the playhead.
    onscrub(scrubMsAt(e.clientX - rect.left, rect.width, durationMs))
  }
</script>

<div
  bind:this={bar}
  bind:clientWidth={widthPx}
  class="relative h-10 cursor-pointer overflow-hidden rounded bg-surface-2"
  onpointerdown={onPointerDown}
  role="slider"
  tabindex="0"
  aria-label="the whole source — click to move the playhead"
  aria-valuemin={0}
  aria-valuemax={durationMs}
  aria-valuenow={draft.startMs}
>
  {#each rallies as r (r.id)}
    <div
      class="pointer-events-none absolute top-2 bottom-2 rounded bg-fg/25"
      style={`left:${xFor(r.start_ms)}px;width:${widthFor(r.start_ms, r.end_ms, 1)}px`}
    ></div>
  {/each}

  <!-- The draft wears the same outline-and-fill the current rally wears in
       ZoomBand, because it is the same object at a different stage: state
       is fill, outline and weight here, never a hue. -->
  <div
    class="pointer-events-none absolute top-1 bottom-1 rounded border-2 border-fg bg-fg/20"
    style={`left:${xFor(draft.startMs)}px;width:${widthFor(
      draft.startMs,
      draft.endMs,
      MIN_DRAW_PX,
    )}px`}
  ></div>

  <!-- Static initial position only; overwritten before first paint by
       TimelineMode's playhead write. A reactive `style` here would clobber
       that imperative write on every unrelated re-render (Finding 3). -->
  <div
    bind:this={playheadEl}
    class="pointer-events-none absolute inset-y-0 w-0.5 bg-fg"
    style={`left:${msToFraction(draft.startMs, durationMs) * 100}%`}
  ></div>
</div>
