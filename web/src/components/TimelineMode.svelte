<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { debounce } from '../lib/debounce'
  import { formatTs, frameStep } from '../lib/time'
  import { msToFraction, toSessionMs, zoomWindow } from '../lib/timeline'
  import type { Rally, SessionDetail } from '../lib/types'
  import OverviewBand from './OverviewBand.svelte'
  import ScoreCurve from './ScoreCurve.svelte'
  import VideoDeck from './VideoDeck.svelte'
  import ZoomBand from './ZoomBand.svelte'

  interface Props {
    detail: SessionDetail
    rallyId: string
    onclose: () => void
  }

  let { detail, rallyId, onclose }: Props = $props()

  const ZOOM_SPAN_MS = 40000
  const SCORE_DEBOUNCE_MS = 150
  const DEFAULT_THRESHOLD = 0.45

  let currentId = $state(untrack(() => rallyId))
  let rallies = $state<Rally[]>(untrack(() => [...detail.rallies]))
  let deck = $state<VideoDeck>()
  let zoomBand = $state<ZoomBand>()
  let scores = $state<number[]>([])
  let scoreStepMs = $state(200)
  let threshold = $state(DEFAULT_THRESHOLD)

  // Falls back to the first rally when `currentId` no longer exists in
  // `rallies` -- Session.svelte remounts this component fresh (via `{#key}`)
  // whenever the rally set is actually replaced, but this guards the one
  // render where a stale `rallyId` prop could otherwise outlive that swap
  // and crash on a `.find()!`.
  const rally = $derived(rallies.find((r) => r.id === currentId) ?? rallies[0])
  const source = $derived(detail.sources.find((s) => s.id === rally?.source_id))
  const win = $derived(
    rally && source
      ? zoomWindow((rally.start_ms + rally.end_ms) / 2, ZOOM_SPAN_MS, source.duration_ms)
      : { startMs: 0, endMs: 0 },
  )
  const neighbours = $derived(
    rally
      ? rallies.filter(
          (r) =>
            r.source_id === rally.source_id &&
            r.id !== rally.id &&
            r.end_ms >= win.startMs &&
            r.start_ms <= win.endMs,
        )
      : [],
  )

  function loadScores(sourceId: string, th: number) {
    api
      .scores(sourceId, th)
      .then((s) => {
        scores = s.scores
        scoreStepMs = s.step_ms
      })
      .catch(() => {
        scores = []
      })
  }

  // The scores endpoint costs real time (~83ms at one-hour scale: parsing
  // features.jsonl + scoring), so it is fetched on mount and whenever the
  // rally's source changes -- never per-render. `threshold` is read
  // untracked here so a slider drag cannot retrigger this effect; threshold
  // changes go through the separately debounced path below instead.
  $effect(() => {
    if (!source) return
    loadScores(source.id, untrack(() => threshold))
  })

  const debouncedLoadScores = debounce(loadScores, SCORE_DEBOUNCE_MS)
  $effect(() => {
    return () => debouncedLoadScores.cancel()
  })

  function onThresholdInput(value: number) {
    // The dashed line moves immediately -- it's pure local math, no
    // network. Only the (currently redundant, since score(t) does not
    // depend on threshold) refetch is debounced, per the endpoint's cost.
    threshold = value
    if (source) debouncedLoadScores(source.id, value)
  }

  // The playhead is written straight to the DOM (via ZoomBand's exported
  // setter), never through Svelte state: VideoDeck's onprogress can fire at
  // up to ~60Hz, and routing that through $state would re-render this whole
  // mode every frame -- the same reasoning as QueueMode's progress bar.
  function writePlayhead(ms: number): void {
    if (!rally) return
    zoomBand?.setPlayheadFraction(msToFraction(ms - win.startMs, Math.max(1, win.endMs - win.startMs)))
  }

  $effect(() => {
    // Reset the playhead to the rally's start whenever the focused rally
    // (or its zoom window) changes.
    if (rally) writePlayhead(rally.start_ms)
  })

  // Local-only: updates the boundary box position during a drag. Must not
  // itself persist -- a drag fires this on every pointermove, and POSTing
  // on each of those would flood the server and race itself (an in-flight
  // early request could resolve after the final one and clobber it).
  function updateBoundsLocal(startMs: number, endMs: number): void {
    rallies = rallies.map((r) =>
      r.id === currentId ? { ...r, start_ms: startMs, end_ms: endMs } : r,
    )
  }

  // The persisted counterpart -- called once per discrete action: a drag
  // release (ZoomBand's `oncommit`) or a `[`/`]` keypress, never mid-drag.
  async function commitBounds(startMs: number, endMs: number): Promise<void> {
    updateBoundsLocal(startMs, endMs)
    try {
      await api.setBounds(currentId, Math.round(startMs), Math.round(endMs))
    } catch (e) {
      console.error('failed to save bounds', e)
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if (!rally || !source) return
    switch (e.key) {
      case 'Escape':
        onclose()
        break
      case '[':
        commitBounds(deck?.currentMs() ?? rally.start_ms, rally.end_ms)
        break
      case ']':
        commitBounds(rally.start_ms, deck?.currentMs() ?? rally.end_ms)
        break
      case ',': {
        const ms = frameStep(deck?.currentMs() ?? 0, source.fps, -1)
        deck?.seekTo(ms)
        writePlayhead(ms)
        break
      }
      case '.': {
        const ms = frameStep(deck?.currentMs() ?? 0, source.fps, 1)
        deck?.seekTo(ms)
        writePlayhead(ms)
        break
      }
      case ' ':
        e.preventDefault()
        if (deck?.paused()) deck?.play()
        else deck?.pause()
        break
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#if rally && source}
  <VideoDeck
    bind:this={deck}
    src={api.proxyUrl(detail.session.id, source.idx)}
    startMs={rally.start_ms}
    endMs={rally.end_ms}
    onended={() => writePlayhead(rally.end_ms)}
    onprogress={() => writePlayhead(deck?.currentMs() ?? 0)}
  />

  <p class="mt-2 font-mono text-xs text-neutral-400">
    rally {rally.idx} · {formatTs(rally.start_ms)} → {formatTs(rally.end_ms)}
  </p>

  <section class="mt-4 space-y-1">
    <p class="text-[11px] tracking-wide text-neutral-500 uppercase">whole session</p>
    <OverviewBand
      {rallies}
      sources={detail.sources}
      currentId={rally.id}
      windowStartMs={toSessionMs(detail.sources, rally.source_id, win.startMs)}
      windowEndMs={toSessionMs(detail.sources, rally.source_id, win.endMs)}
      onpick={(id) => (currentId = id)}
    />
  </section>

  <section class="mt-4 space-y-1">
    <p class="text-[11px] tracking-wide text-neutral-500 uppercase">±20s — drag either handle</p>
    <ZoomBand
      bind:this={zoomBand}
      {rally}
      {neighbours}
      windowStartMs={win.startMs}
      windowEndMs={win.endMs}
      onchange={updateBoundsLocal}
      oncommit={commitBounds}
      onscrub={(ms) => {
        deck?.seekTo(ms)
        writePlayhead(ms)
      }}
    />
    <ScoreCurve
      {scores}
      {threshold}
      stepMs={scoreStepMs}
      windowStartMs={win.startMs}
      windowEndMs={win.endMs}
    />

    <div class="flex items-center gap-2 pt-1">
      <span class="text-[11px] text-neutral-500">preview threshold</span>
      <input
        type="range"
        min="0.05"
        max="0.95"
        step="0.01"
        value={threshold}
        oninput={(e) => onThresholdInput(Number(e.currentTarget.value))}
        class="flex-1"
        aria-label="preview threshold"
      />
      <span class="w-10 font-mono text-[11px] text-neutral-500">{threshold.toFixed(2)}</span>
    </div>

    <p class="font-mono text-[11px] text-neutral-500">
      detector score — dashed line is the threshold · [ ] set in/out · , . step one frame · esc back
    </p>
  </section>
{:else}
  <p class="text-sm text-neutral-400">No rallies to show.</p>
{/if}
