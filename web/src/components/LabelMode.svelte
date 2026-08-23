<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { isEditableTarget } from '../lib/keyboard'
  import { FLAG_ORDER, LabelController, LabelWriter } from '../lib/labels'
  import { fractionToScrubMs, scrubMsToFraction } from '../lib/scrub'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import { formatDuration, formatTs } from '../lib/time'
  import type { BoundaryFlag, LabelAction, Verdict } from '../lib/labels'
  import type { LabelRecord, SessionDetail, Source } from '../lib/types'
  import KeyHints from './KeyHints.svelte'
  import VideoDeck from './VideoDeck.svelte'

  interface Props {
    detail: SessionDetail
    onclose: () => void
    /** Open on this rally instead of the first one, when it is still in this
     * session's (unfiltered) rally list. Session passes the rally that had
     * focus in the queue when `l` opened this mode -- the M4 fix for the
     * regression where that id was captured into focusedRallyId and then
     * used only for the return trip, never threaded through to here.
     * `null` (M3: `l` now works with no current rally, e.g. from the
     * "Session reviewed" screen) falls through to index 0 the same way an
     * id LabelController.jumpTo can't find would. */
    startAtRallyId?: string | null
  }
  let { detail, onclose, startAtRallyId = null }: Props = $props()

  const VERDICT_KEYS: Record<string, Verdict> = {
    '1': 'clean',
    '2': 'not_play',
    '3': 'partly',
    '4': 'unsure',
  }
  // Left-hand keys are the clip's start and right-hand keys its end; the
  // first of each pair is early and the second late.
  const FLAG_KEYS: Record<string, BoundaryFlag> = {
    q: 'start_early',
    w: 'start_late',
    o: 'end_early',
    p: 'end_late',
  }
  const FLAG_LABELS: Record<BoundaryFlag, string> = {
    start_early: 'starts early (Q)',
    start_late: 'starts late (W)',
    end_early: 'ends early (O)',
    end_late: 'ends late (P)',
  }

  const toaster = createToaster()
  // One queue for the whole mode, living across controller rebuilds: it
  // serialises writes per rally so a fast burst of keystrokes cannot reach
  // the server out of order, and remembers what the server last accepted so
  // a failure restores that rather than a state no reader ever held. Not
  // $state -- nothing renders from it.
  const writer = new LabelWriter(api)
  let controller = $state<LabelController | null>(null)
  let loadError = $state<string | null>(null)
  let version = $state(0)
  let deck = $state<VideoDeck>()
  let scrubTrack = $state<HTMLDivElement>()
  let scrubFill = $state<HTMLDivElement>()
  let elapsedEl = $state<HTMLSpanElement>()
  // Not $state: read only from the pointer handlers below, never from the
  // template, so making it reactive would just be a signal nothing ever
  // subscribes to.
  let scrubbing = false

  // One fetch per source, merged. Labels are per-source and a session can
  // hold several; the controller matches them to rallies by exact detector
  // span, so a flat list is all it needs.
  $effect(() => {
    const sources = untrack(() => detail.sources)
    const rallies = untrack(() => detail.rallies)
    const startAt = untrack(() => startAtRallyId)
    Promise.all(sources.map((s) => api.sourceLabels(s.id)))
      .then((lists) => {
        const flat: LabelRecord[] = lists.flat()
        controller = new LabelController(rallies, flat)
        // Mirrors QueueMode's `if (initialRallyId) queue.jumpTo(...)` --
        // jumpTo itself already no-ops for an id it can't find, and the
        // guard here means null (no rally was focused) takes the same
        // path, leaving the controller on its constructor default of 0.
        if (startAt) controller.jumpTo(startAt)
        version += 1
      })
      .catch((e) => (loadError = String(e)))
  })

  const current = $derived.by(() => {
    version
    return controller?.current
  })
  const verdict = $derived.by(() => {
    version
    return controller?.currentVerdict ?? null
  })
  const flags = $derived.by(() => {
    version
    return controller?.currentFlags ?? []
  })
  const flagsEnabled = $derived.by(() => {
    version
    return controller?.flagsEnabled ?? false
  })
  const stats = $derived.by(() => {
    version
    return {
      index: controller?.index ?? 0,
      total: controller?.total ?? 0,
      labelled: controller?.labelledCount ?? 0,
    }
  })

  function srcFor(sourceId: string): string {
    const s: Source | undefined = detail.sources.find((x) => x.id === sourceId)
    return s ? api.proxyUrl(detail.session.id, s.idx) : ''
  }

  // Paints the fill and the elapsed readout for an absolute timestamp.
  // Written straight to the DOM, same rule QueueMode's progress bar and
  // ZoomBand's playhead follow: `onprogress` fires up to ~60Hz, and routing
  // that through Svelte state would re-render this whole panel every frame.
  function renderScrubAt(ms: number): void {
    if (!current) return
    const fraction = scrubMsToFraction(ms, current.det_start_ms, current.det_end_ms)
    if (scrubFill) scrubFill.style.transform = `scaleX(${fraction})`
    if (elapsedEl) elapsedEl.textContent = formatTs(ms - current.det_start_ms)
  }

  // VideoDeck's onprogress already reports a 0..1 fraction computed against
  // exactly this rally's span (startMs/endMs below are det_start_ms/
  // det_end_ms), so this is the play-tracking half of the scrub bar --
  // pointer drags go through seekToFraction instead, which is the same
  // round trip in the other direction.
  function onProgress(fraction: number): void {
    if (!current) return
    renderScrubAt(fractionToScrubMs(fraction, current.det_start_ms, current.det_end_ms))
  }

  function scrubFraction(e: PointerEvent): number {
    if (!scrubTrack) return 0
    const rect = scrubTrack.getBoundingClientRect()
    return (e.clientX - rect.left) / rect.width
  }

  // Shared by click-to-seek and every pointermove of a drag: seek the deck,
  // then paint immediately rather than waiting for the next onprogress tick
  // (~16ms away but a visible stutter on a drag, which fires far faster than
  // that).
  function seekToFraction(fraction: number): void {
    if (!current) return
    const ms = fractionToScrubMs(fraction, current.det_start_ms, current.det_end_ms)
    deck?.seekTo(ms)
    renderScrubAt(ms)
  }

  function onScrubDown(e: PointerEvent): void {
    if (!scrubTrack) return
    scrubbing = true
    scrubTrack.setPointerCapture(e.pointerId)
    seekToFraction(scrubFraction(e))
  }

  function onScrubMove(e: PointerEvent): void {
    if (!scrubbing) return
    seekToFraction(scrubFraction(e))
  }

  function endScrub(e: PointerEvent): void {
    scrubbing = false
    scrubTrack?.releasePointerCapture(e.pointerId)
  }

  async function apply(action: LabelAction | null): Promise<void> {
    version += 1
    if (!action) return
    const outcome = await writer.submit(action)
    if (outcome.status === 'failed') {
      // Same reasoning as QueueMode: put this action's rally back without
      // moving the cursor or consuming the undo stack, surface it, and do
      // not halt the pass over a rare failure on a LAN box.
      //
      // `restore` rather than `revert(action)`: the writer hands back the
      // last state the server actually accepted, which after a burst where
      // several writes failed is not this action's own predecessor.
      // 'superseded' is deliberately silent -- a newer action for the same
      // rally is still queued and carries the whole state, so the reviewer
      // has nothing to fix and nothing to be told about.
      controller?.restore(action.rallyId, outcome.restore)
      version += 1
      toaster.push("Couldn't save that label -- reverted")
    }
  }

  function onKey(e: KeyboardEvent) {
    if (isEditableTarget(e.target)) return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if (!controller) return

    const v = VERDICT_KEYS[e.key]
    if (v) {
      apply(controller.setVerdict(v))
      return
    }
    const f = FLAG_KEYS[e.key.toLowerCase()]
    if (f) {
      apply(controller.toggleFlag(f))
      return
    }
    switch (e.key) {
      case 'r':
      case 'R':
        deck?.replay()
        break
      case 'ArrowRight':
        e.preventDefault()
        controller.next()
        version += 1
        break
      case 'ArrowLeft':
        e.preventDefault()
        controller.back()
        version += 1
        break
      case 'u':
      case 'U':
        apply(controller.undo())
        break
      case ' ':
        e.preventDefault()
        if (deck?.paused()) deck?.play()
        else deck?.pause()
        break
      case 'l':
      case 'L':
      case 'Escape':
        onclose()
        break
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#if loadError}
  <p class="rounded bg-danger/10 p-3 text-body text-danger">{loadError}</p>
{:else if !controller}
  <p class="text-body text-dim">Loading labels…</p>
{:else if !current}
  <section class="rounded-lg border border-line p-8 text-center">
    <h2 class="text-title font-semibold">Nothing to label</h2>
    <p class="mt-2 font-data text-body text-dim">
      This session has no rallies yet. Detect or re-segment a source first.
    </p>
  </section>
{:else}
  <div class="relative">
    <!--
      onended replays instead of advancing, so playback loops inside the
      span. label.html did this deliberately: the boundary is part of what
      is being judged, and letting the video run on means judging the next
      rally by accident -- which matters most for the end_late case these
      flags exist to capture.

      `speed` is deliberately left at its default of 1. At 2x a reach and a
      swing are not reliably distinguishable, and a wrong label is worse
      than a slow pass.
    -->
    <VideoDeck
      bind:this={deck}
      src={srcFor(current.source_id)}
      startMs={current.det_start_ms}
      endMs={current.det_end_ms}
      onended={() => deck?.replay()}
      onprogress={onProgress}
    />
    <div
      class="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-1
             font-data text-data tabular-nums text-fg"
    >
      {stats.index + 1} / {stats.total} · {stats.labelled} labelled
    </div>
  </div>

  <!--
    Taller and more saturated than QueueMode's progress strip on purpose
    (that one is a passive indicator; this one has to read as draggable at a
    glance). pointerdown/pointermove drive both the click-to-seek and the
    drag, not onclick -- matching ZoomBand's onscrub, and it's what lets a
    plain tabindex="0" + role="slider" satisfy svelte-check's a11y checks
    with no keyboard handler: the click-events-have-key-events rule only
    fires for onclick.

    Deliberately no arrow-key seeking on this control -- ArrowLeft/Right are
    prev/next rally in this mode (see onKey above), and giving them a second
    meaning here would make them do different things depending on which
    element happens to have focus.
  -->
  <div
    bind:this={scrubTrack}
    class="relative mt-3 h-3 cursor-ew-resize rounded bg-surface-2"
    onpointerdown={onScrubDown}
    onpointermove={onScrubMove}
    onpointerup={endScrub}
    onpointercancel={endScrub}
    role="slider"
    tabindex="0"
    aria-label="scrub within rally"
    aria-valuemin={current.det_start_ms}
    aria-valuemax={current.det_end_ms}
    aria-valuenow={current.det_start_ms}
  >
    <div
      bind:this={scrubFill}
      class="h-full origin-left rounded bg-accent"
      style="transform: scaleX(0)"
    ></div>
  </div>

  <!--
    Confidence is deliberately not rendered. Full blinding would be
    pointless -- you already know these spans are detector output -- but
    the highest-confidence window in the existing fixture is a false
    positive (someone walking past the lens), so seeing the number before
    judging is a bias with no compensating benefit.
  -->
  <div class="mt-2 font-data text-data text-dim">
    {formatTs(current.det_start_ms)} ·
    {formatDuration(current.det_end_ms - current.det_start_ms)} ·
    <span bind:this={elapsedEl}>{formatTs(0)}</span> / {formatTs(current.det_end_ms - current.det_start_ms)}
  </div>

  <div class="mt-3 flex flex-wrap gap-2">
    {#each Object.entries(VERDICT_KEYS) as [key, v] (v)}
      <button
        class="rounded border px-3 py-1 font-data text-body
               {verdict === v
          ? 'border-accent bg-accent/20 text-accent'
          : 'border-line text-dim hover:bg-surface-2'}"
        onclick={() => apply(controller?.setVerdict(v) ?? null)}
      >
        {v} <span class="text-faint">{key}</span>
      </button>
    {/each}
  </div>

  <div class="mt-2 flex flex-wrap gap-2">
    {#each FLAG_ORDER as f (f)}
      <button
        disabled={!flagsEnabled}
        class="rounded border px-3 py-1 font-data text-data disabled:opacity-30
               {flags.includes(f)
          ? 'border-accent bg-accent/20 text-accent'
          : 'border-line text-dim hover:bg-surface-2'}"
        onclick={() => apply(controller?.toggleFlag(f) ?? null)}
      >
        {FLAG_LABELS[f]}
      </button>
    {/each}
  </div>

  <KeyHints mode="label" />
{/if}

{#if toaster.toasts.length > 0}
  <div class="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
    {#each toaster.toasts as t (t.id)}
      <div class="rounded {toastToneClasses(t.tone)} px-3 py-2 text-body shadow-lg">
        {t.message}
      </div>
    {/each}
  </div>
{/if}
