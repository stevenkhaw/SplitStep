<script lang="ts">
  import { untrack } from 'svelte'
  import { movePoint, pointFromClient, polygonClipPath } from '../lib/quad'
  import { formatTs, frameStep } from '../lib/time'

  interface Props {
    frameSrc: string
    timeMs: number
    maxMs: number
    fps: number
    points: [number, number][]
    alt: string
    onpoints: (p: [number, number][]) => void
    onseek: (ms: number) => void
  }

  let { frameSrc, timeMs, maxMs, fps, points, alt, onpoints, onseek }: Props = $props()

  let dragging = $state<number | null>(null)
  let wrap = $state<HTMLDivElement>()
  let frameError = $state(false)

  // The slider's live readout during a drag. `timeMs` is the committed
  // value -- it only moves on `onchange` (release), since each distinct
  // value is one ffmpeg extraction against a 4K original. Without a
  // separate draft, the timecode text next to the slider would freeze at
  // the pre-drag value for the whole drag even though the thumb itself
  // (native browser behavior, unrelated to this binding) tracks the
  // pointer the entire time -- found live: the thumb moved, the numbers
  // next to it didn't. `draftMs` re-syncs to `timeMs` via the effect below
  // whenever the commit lands, whether that came from this slider's own
  // `onchange`, the frame-step buttons, or a grid-tile click elsewhere in
  // the wizard.
  let draftMs = $state(untrack(() => timeMs))
  $effect(() => {
    draftMs = timeMs
  })

  // Slider granularity of one frame, so keyboard arrows on the slider step
  // frame by frame the same way the frame buttons do.
  const stepMs = $derived(fps > 0 ? Math.max(1, Math.round(1000 / fps)) : 1)

  function stepFrames(dir: 1 | -1): void {
    onseek(frameStep(timeMs, fps, dir))
  }

  const polygon = $derived(polygonClipPath(points))

  function at(e: PointerEvent): [number, number] {
    if (!wrap) return [0, 0]
    return pointFromClient(e.clientX, e.clientY, wrap.getBoundingClientRect())
  }

  // Pointer capture and the move/up listeners live on the handle button
  // itself (a real interactive, focusable element), not on the wrapping
  // div -- so the drag keeps tracking the pointer even once it leaves the
  // button's own bounds, without needing pointer handlers on a static
  // `<div>` (which svelte-check's a11y check correctly flags: a plain div
  // with pointer handlers and no interactive role is not something a
  // keyboard or screen-reader user can operate). `at()` still measures
  // against `wrap`'s rect regardless of which element the event fired on,
  // since PointerEvent.clientX/Y are viewport-relative.
  function onHandleDown(e: PointerEvent, i: number) {
    dragging = i
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }

  function onHandleMove(e: PointerEvent, i: number) {
    if (dragging !== i) return
    onpoints(movePoint(points, i, at(e)))
  }

  function endDrag() {
    dragging = null
  }
</script>

<!--
  No fixed aspect ratio (e.g. Tailwind's `aspect-video`) and no
  `object-fit` on the image: the wrapper has no height of its own, so
  it sizes to exactly the image's rendered box (block img,
  `width:100%; height:auto`). That means this wrapper's own
  getBoundingClientRect() -- what `at()` and every handle's percentage
  position are computed against -- always equals the frame image's
  actual displayed rect, whatever the source video's aspect ratio is.
  An `object-contain` image inside a fixed-aspect box would letterbox
  when the two ratios differ, silently offsetting every corner from
  where the pointer actually is.
-->
<div bind:this={wrap} class="relative mt-3 w-full min-h-48 select-none bg-black">
  <!--
    No `overflow-hidden` here, deliberately: the default trapezoid (and
    any preset) puts two corner handles at y=1.0 -- the bottom edge of
    `wrap`, on purpose (see the module comment on DEFAULT_QUAD_POINTS).
    A handle centered exactly on that edge has half its hit area below
    it; `overflow-hidden` on this element would clip that half away,
    found live: a real pointer down dead-center on such a handle hit
    the `<section>` behind it, not the button, because the clip
    boundary sat right at the handle's own center. Rounding lives on
    the image itself instead, which needs no clipping parent to look
    rounded.
  -->
  <img
    src={frameSrc}
    {alt}
    class="block w-full h-auto rounded"
    draggable="false"
    onerror={() => (frameError = true)}
    onload={() => (frameError = false)}
  />
  <div
    class="pointer-events-none absolute inset-0 bg-blue-400/20"
    style={`clip-path: ${polygon}`}
  ></div>
  {#each points as p, i (i)}
    <button
      class="absolute h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-grab rounded-full
             border-2 border-white bg-blue-500 touch-none"
      style={`left:${p[0] * 100}%;top:${p[1] * 100}%`}
      onpointerdown={(e) => onHandleDown(e, i)}
      onpointermove={(e) => onHandleMove(e, i)}
      onpointerup={endDrag}
      onpointercancel={endDrag}
      aria-label={`corner ${i + 1}`}
    ></button>
  {/each}
</div>

<!--
  The frame this region is drawn over is scrubbable, not fixed at t=0:
  phone footage habitually starts on a black or pocketed frame, and a
  region cannot be placed against one. The buttons step by exactly one
  frame (and by a second) for lining a corner up against a player's
  feet; the slider covers the clip. Server-side each distinct timestamp
  is one ffmpeg extraction, so the slider only fetches on release
  (`onchange`) while dragging leaves the commit alone.
-->
<div class="mt-2 flex items-center gap-2">
  <button
    class="rounded border border-neutral-700 px-2 py-0.5 font-mono text-xs
           hover:bg-neutral-800"
    onclick={() => onseek(timeMs - 1000)}
    aria-label="back one second"
  >
    &laquo; 1s
  </button>
  <button
    class="rounded border border-neutral-700 px-2 py-0.5 font-mono text-xs
           hover:bg-neutral-800"
    onclick={() => stepFrames(-1)}
    aria-label="previous frame"
  >
    &lsaquo; fr
  </button>
  <input
    type="range"
    min="0"
    max={maxMs}
    step={stepMs}
    value={timeMs}
    oninput={(e) => (draftMs = e.currentTarget.valueAsNumber)}
    onchange={(e) => onseek(e.currentTarget.valueAsNumber)}
    class="min-w-0 flex-1 accent-blue-500"
    aria-label="frame timestamp"
  />
  <button
    class="rounded border border-neutral-700 px-2 py-0.5 font-mono text-xs
           hover:bg-neutral-800"
    onclick={() => stepFrames(1)}
    aria-label="next frame"
  >
    fr &rsaquo;
  </button>
  <button
    class="rounded border border-neutral-700 px-2 py-0.5 font-mono text-xs
           hover:bg-neutral-800"
    onclick={() => onseek(timeMs + 1000)}
    aria-label="forward one second"
  >
    1s &raquo;
  </button>
  <span class="w-20 shrink-0 text-right font-mono text-xs text-neutral-400">
    {formatTs(draftMs)}
  </span>
</div>

{#if frameError}
  <p class="mt-2 font-mono text-xs text-amber-300">
    No frame at {formatTs(timeMs)} -- the proxy may still be transcoding. Try again, or
    scrub somewhere else.
  </p>
{/if}
