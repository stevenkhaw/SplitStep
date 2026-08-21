<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { debounce } from '../lib/debounce'
  import { isEditableTarget } from '../lib/keyboard'
  import { createToaster } from '../lib/toaster.svelte'
  import { formatTs, frameStep } from '../lib/time'
  import { clampMinGap, msToFraction, toSessionMs, zoomWindow } from '../lib/timeline'
  import type { Rally, SessionDetail } from '../lib/types'
  import OverviewBand from './OverviewBand.svelte'
  import ScoreCurve from './ScoreCurve.svelte'
  import VideoDeck from './VideoDeck.svelte'
  import ZoomBand from './ZoomBand.svelte'

  interface Props {
    detail: SessionDetail
    rallyId: string
    /** Overrides the initial `rallies` seed, e.g. QueueMode's live-merged
     * snapshot (see QueueController.liveSnapshot) so a rally starred/
     * rejected earlier in this queue session renders correctly in
     * OverviewBand instead of `detail.rallies`' stale server-snapshot
     * flags (Finding: OverviewBand colored from a snapshot). Falls back to
     * `detail.rallies` for any other caller (e.g. a direct mount in tests). */
    initialRallies?: Rally[]
    onclose: () => void
  }

  let { detail, rallyId, initialRallies, onclose }: Props = $props()

  const ZOOM_SPAN_MS = 40000
  const SCORE_DEBOUNCE_MS = 150
  const SAVED_NOTICE_MS = 1500

  const toaster = createToaster()
  // 'error' is sticky until the next successful save, unlike 'saved' which
  // clears itself: a failed edit is not something to glance past.
  let saveState = $state<'idle' | 'saved' | 'error'>('idle')
  let saveTimer: ReturnType<typeof setTimeout> | undefined

  let currentId = $state(untrack(() => rallyId))
  let rallies = $state<Rally[]>(untrack(() => [...(initialRallies ?? detail.rallies)]))
  let deck = $state<VideoDeck>()
  let zoomBand = $state<ZoomBand>()
  let scores = $state<number[]>([])
  let scoreStepMs = $state(200)
  // Null until the first /scores response supplies it. The two camera
  // profiles put the threshold on different scales (0.25 subject, 0.45 pair),
  // so a constant here is wrong for half of all sources -- the API resolves
  // it per source and this is where that answer lands.
  let threshold = $state<number | null>(null)

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

  // Snapshot of `win`, held fixed for the duration of a ZoomBand drag (see
  // onZoomDragStart/onZoomDragEnd below). Finding 3: `win` recenters on
  // every pointermove (its midpoint depends on `rally.start_ms/end_ms`,
  // which `updateBoundsLocal` reassigns each frame), so without freezing it
  // the coordinate space ZoomBand measures the pointer against shifts by
  // half of every move -- each move amplifies the last, and the handle ends
  // up travelling roughly 2x the pointer. `effectiveWin` is what every
  // consumer below renders against; `win` itself is only ever read to take
  // a snapshot from or to recompute once a drag ends.
  let frozenWindow = $state<{ startMs: number; endMs: number } | null>(null)
  const effectiveWin = $derived(frozenWindow ?? win)

  const neighbours = $derived(
    rally
      ? rallies.filter(
          (r) =>
            r.source_id === rally.source_id &&
            r.id !== rally.id &&
            r.end_ms >= effectiveWin.startMs &&
            r.start_ms <= effectiveWin.endMs,
        )
      : [],
  )

  function onZoomDragStart(): void {
    frozenWindow = { startMs: win.startMs, endMs: win.endMs }
  }

  function onZoomDragEnd(): void {
    frozenWindow = null
  }

  function loadScores(sourceId: string, th: number | null) {
    api
      .scores(sourceId, th ?? undefined)
      .then((s) => {
        // /scores' cost scales with features.jsonl's length, so responses
        // are not guaranteed to land in request order: switch (via
        // OverviewBand) from a long source to a short one and the long
        // one's response routinely arrives second. scoredSourceId is
        // reassigned synchronously before this call goes out (see the
        // $effect below), so by the time any response lands it names
        // whichever source is *currently* selected -- a mismatch means a
        // later switch already superseded this one.
        if (sourceId !== scoredSourceId) return
        scores = s.scores
        scoreStepMs = s.step_ms
        // Only adopt the server's echoed threshold when we asked it to
        // resolve the profile default (th === null). Writing it back
        // unconditionally -- including for an explicit slider value we
        // already applied locally in onThresholdInput -- is itself a race:
        // a debounced response for an in-flight drag can land after the
        // user has moved the slider further and snap the thumb backward.
        if (th === null) threshold = s.threshold
      })
      .catch(() => {
        if (sourceId !== scoredSourceId) return
        scores = []
      })
  }

  // Plain bookkeeping, not $state: reading/writing it must never itself
  // create or satisfy a reactive dependency. It exists only so the effect
  // below can tell a genuine source switch (picking a rally that belongs to
  // a different source) apart from `source` merely getting a new object
  // identity for the *same* id, which comparing derived-`source` reference
  // identity alone would conflate with a switch.
  let scoredSourceId: string | undefined

  // The scores endpoint costs real time (~83ms at one-hour scale: parsing
  // features.jsonl + scoring), so it is fetched on mount and whenever the
  // rally's source changes -- never per-render. `threshold` is read
  // untracked here so a slider drag cannot retrigger this effect; threshold
  // changes go through the separately debounced path below instead.
  //
  // A genuine switch to a different source resets `threshold` to null
  // first, so the call below omits it and asks the API to resolve *that*
  // source's own profile default -- first call is per source, not just
  // once per mount. Carrying over the previous source's numeric value here
  // would be sent as an explicit override, which the server just echoes
  // back (see loadScores' comment above), silently wrong-scale whenever
  // the two sources sit on different camera-view profiles.
  $effect(() => {
    if (!source) return
    if (source.id !== scoredSourceId) threshold = null
    scoredSourceId = source.id
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
    zoomBand?.setPlayheadFraction(
      msToFraction(ms - effectiveWin.startMs, Math.max(1, effectiveWin.endMs - effectiveWin.startMs)),
    )
  }

  $effect(() => {
    // Reset the playhead to the rally's start only when the *focused*
    // rally changes (currentId) -- not on every bounds edit. `rally` is a
    // new object reference on every `rallies = rallies.map(...)` call
    // inside updateBoundsLocal (i.e. every drag frame); depending on it
    // directly (as this used to) reran the effect on each of those and
    // yanked the playhead back to the rally's start mid-drag. `currentId`
    // is a plain state primitive that changes only via OverviewBand's
    // onpick, so it's stable across a bounds edit.
    currentId
    untrack(() => {
      if (rally) writePlayhead(rally.start_ms)
    })
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
  //
  // Runs every commit through clampMinGap regardless of source: a
  // drag-committed pair already satisfies it (ZoomBand's own per-frame
  // clamp), so this is a no-op there, but the keyboard path below calls
  // this directly with an unclamped playhead position -- without this, `[`
  // with the playhead parked past the rally's current end_ms (or `]` with
  // it parked before start_ms) would persist an inverted rally
  // (Finding 7). Applying it here, rather than duplicating a clamp at each
  // keyboard case, is what makes both paths share one rule.
  async function commitBounds(startMs: number, endMs: number): Promise<void> {
    const clamped = clampMinGap(startMs, endMs)
    updateBoundsLocal(clamped.startMs, clamped.endMs)
    try {
      await api.setBounds(currentId, Math.round(clamped.startMs), Math.round(clamped.endMs))
      // There is no save button -- a commit persists immediately -- so without
      // this the user has no way to tell an edit landed. Worse, the catch
      // below used to log to the console only, which made a FAILED save look
      // exactly like a successful one.
      saveState = 'saved'
      if (saveTimer !== undefined) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => {
        saveState = 'idle'
        saveTimer = undefined
      }, SAVED_NOTICE_MS)
    } catch (e) {
      console.error('failed to save bounds', e)
      saveState = 'error'
      toaster.push('Could not save the new boundaries — check that the server is running.')
    }
  }

  function onKey(e: KeyboardEvent) {
    if (isEditableTarget(e.target)) return
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
      windowStartMs={toSessionMs(detail.sources, rally.source_id, effectiveWin.startMs)}
      windowEndMs={toSessionMs(detail.sources, rally.source_id, effectiveWin.endMs)}
      onpick={(id) => (currentId = id)}
    />
  </section>

  <section class="mt-4 space-y-1">
    <p class="text-[11px] tracking-wide text-neutral-500 uppercase">±20s — drag either handle</p>
    <ZoomBand
      bind:this={zoomBand}
      {rally}
      {neighbours}
      windowStartMs={effectiveWin.startMs}
      windowEndMs={effectiveWin.endMs}
      onchange={updateBoundsLocal}
      oncommit={commitBounds}
      ondragstart={onZoomDragStart}
      ondragend={onZoomDragEnd}
      onscrub={(ms) => {
        deck?.seekTo(ms)
        writePlayhead(ms)
      }}
    />
    {#if threshold !== null}
      <ScoreCurve
        {scores}
        {threshold}
        stepMs={scoreStepMs}
        windowStartMs={effectiveWin.startMs}
        windowEndMs={effectiveWin.endMs}
      />
    {/if}

    <div class="flex items-center gap-2 pt-1">
      <span class="text-[11px] text-neutral-500">preview threshold</span>
      <input
        type="range"
        min="0.05"
        max="0.95"
        step="0.01"
        value={threshold ?? 0.05}
        oninput={(e) => onThresholdInput(Number(e.currentTarget.value))}
        disabled={threshold === null}
        class="flex-1 disabled:opacity-40"
        aria-label="preview threshold"
      />
      <span class="w-10 font-mono text-[11px] text-neutral-500">
        {threshold === null ? '…' : threshold.toFixed(2)}
      </span>
    </div>

    <p class="flex items-center gap-2 font-mono text-[11px] text-neutral-500">
      <span>
        detector score — dashed line is the threshold · [ ] set in/out · , . step one frame · esc back
      </span>
      <!-- Edits persist on drag-release and on [ / ], with no save button, so
           this is the only thing telling the user an edit took. -->
      {#if saveState === 'saved'}
        <span class="text-green-400" role="status">✓ saved</span>
      {:else if saveState === 'error'}
        <span class="text-red-400" role="status">✕ not saved</span>
      {/if}
    </p>
  </section>

  {#if toaster.toasts.length > 0}
    <div class="pointer-events-none fixed bottom-4 left-1/2 z-50 -translate-x-1/2 space-y-2">
      {#each toaster.toasts as t (t.id)}
        <div class="rounded bg-red-900/90 px-3 py-2 text-sm text-red-100 shadow-lg">
          {t.message}
        </div>
      {/each}
    </div>
  {/if}
{:else}
  <p class="text-sm text-neutral-400">No rallies to show.</p>
{/if}
