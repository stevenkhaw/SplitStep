<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { debounce } from '../lib/debounce'
  import { isEditableTarget } from '../lib/keyboard'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import { formatTs, frameStep } from '../lib/time'
  import {
    clampMinGap,
    draftSpanAt,
    msToFraction,
    setDraftIn,
    setDraftOut,
    setInPoint,
    setOutPoint,
    toSessionMs,
    zoomWindow,
  } from '../lib/timeline'
  import type { BoundsEdit, DraftSpan } from '../lib/timeline'
  import { applyAdd, applyMerge, applySplit, canMerge, canSplit, findMergePrev } from '../lib/split'
  import type { Rally, SessionDetail } from '../lib/types'
  import OverviewBand from './OverviewBand.svelte'
  import ScoreCurve from './ScoreCurve.svelte'
  import KeyHints from './KeyHints.svelte'
  import SourceScrub from './SourceScrub.svelte'
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
  let sourceScrub = $state<SourceScrub>()

  /**
   * The span `N` is drawing, or null when no add is open. Null is the
   * *mode* flag; the span inside it is never null, because draftSpanAt
   * seeds one at the playhead -- see its comment for why there is no
   * empty-draft case to model.
   */
  let draft = $state<DraftSpan | null>(null)

  /**
   * Where the playhead stood when `N` was pressed, and the only thing the
   * deck's in-point becomes for the duration of the add.
   *
   * The deck has to span the rest of the file while adding -- its out-point
   * is what pauses playback, so with the current rally's end still in place
   * the reviewer could scrub to a gap and then not play it, which is the
   * one thing this mode exists to do. But VideoDeck re-seeks whenever its
   * `startMs` changes, so handing it 0 would throw playback to the top of
   * the file on the keypress. Anchoring on the playhead makes that re-seek
   * land exactly where the playhead already was: no jump, and no `tick()`
   * race to undo one.
   */
  let addAnchorMs = $state(0)
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

  // The session's sources in their own idx order -- what split.ts's
  // renumber needs to reproduce _renumber's ordering exactly. Derived from
  // `detail.sources` rather than from the rally list, which may not contain
  // a rally for every source.
  const sourceOrder = $derived(
    [...detail.sources].sort((a, b) => a.idx - b.idx).map((s) => s.id),
  )

  // The rally immediately before the current one, which is what `U` would
  // merge into -- see split.ts::findMergePrev for why this must be the same
  // lookup applyMerge uses rather than a scan re-derived here. Computed here
  // so the key hint can be greyed before the request rather than after a 400.
  const mergePrev = $derived(rally ? findMergePrev(rallies, rally, sourceOrder) : undefined)

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
    // The scrub bar only exists while an add is open, so this is a no-op
    // the rest of the time. It is written the same imperative way and from
    // the same call, so the two bars cannot disagree about where the
    // playhead is.
    if (source) sourceScrub?.setPlayheadFraction(msToFraction(ms, source.duration_ms))
    if (!rally) return
    zoomBand?.setPlayheadFraction(
      msToFraction(ms - effectiveWin.startMs, Math.max(1, effectiveWin.endMs - effectiveWin.startMs)),
    )
  }

  // The "saved" notice, armed by every path that persists something:
  // bounds, a split and an add all have no save button, so this line is the
  // only thing telling the reviewer the write landed. One function rather
  // than a third copy of the same four lines.
  function markSaved(): void {
    saveState = 'saved'
    if (saveTimer !== undefined) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveState = 'idle'
      saveTimer = undefined
    }, SAVED_NOTICE_MS)
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
      markSaved()
    } catch (e) {
      console.error('failed to save bounds', e)
      saveState = 'error'
      toaster.push('Could not save the new boundaries — check that the server is running.')
    }
  }

  // Refusals are surfaced, never silent. The whole reason `[` could destroy a
  // rally is that it did so with no confirmation, no undo and no save notice,
  // so a refusal that said nothing would be only a smaller version of the same
  // bug.
  function applyEdit(edit: BoundsEdit): void {
    if (!edit.ok) {
      toaster.push(edit.reason)
      return
    }
    commitBounds(edit.startMs, edit.endMs)
  }

  // Both handlers update `rallies` locally rather than asking Session to
  // remount: a remount resets the playhead (see the $effect on currentId),
  // which would throw the reviewer back to the top of the rally at exactly
  // the moment they want to trim the seam they just made. Session's
  // closeTimeline already refetches on Esc, so the queue sees both halves
  // with no wiring here.
  async function splitHere(): Promise<void> {
    if (!rally) return
    const atMs = Math.round(deck?.currentMs() ?? rally.start_ms)
    if (!canSplit(rally, atMs)) {
      toaster.push('The playhead is too close to a boundary to split here.')
      return
    }
    try {
      const { new_rally_id } = await api.splitRally(rally.id, atMs)
      rallies = applySplit(rallies, rally.id, atMs, new_rally_id, sourceOrder)
      markSaved()
      toaster.push(`Split at ${formatTs(atMs)} — U to merge back`)
    } catch (e) {
      console.error('failed to split rally', e)
      saveState = 'error'
      toaster.push('Could not split this rally — check that the server is running.')
    }
  }

  async function mergeBack(): Promise<void> {
    if (!rally) return
    if (!canMerge(rally, mergePrev)) {
      // Two different refusals, and the reviewer's next move differs, so
      // they must not collapse into one sentence.
      toaster.push(
        rally.det_start_ms !== null
          ? 'This rally came from the detector — only a half you split can be merged back.'
          : 'Nothing abuts the start of this rally to merge it into.',
      )
      return
    }
    const merging = rally.id
    try {
      await api.mergeRally(merging)
      // Land on the survivor before the row disappears, or `rally` falls
      // back to rallies[0] and the reviewer is silently moved to the top of
      // the session.
      currentId = mergePrev!.id
      rallies = applyMerge(rallies, merging, sourceOrder)
      toaster.push('Merged back into the previous rally')
    } catch (e) {
      console.error('failed to merge rally', e)
      saveState = 'error'
      toaster.push('Could not merge this rally — check that the server is running.')
    }
  }

  /**
   * `N`: start drawing a span the detector never proposed.
   *
   * The draft is seeded rather than left empty (draftSpanAt), so `[` and
   * `]` below are the same two functions an existing rally's bounds go
   * through -- no second in/out vocabulary, which is the whole reason this
   * mode lives in TimelineMode rather than somewhere of its own.
   */
  function startAdd(): void {
    if (!source) return
    // A second `N` cannot re-seed: the open draft is the only copy of a
    // span the reviewer picked by eye. It says so rather than going dead.
    if (draft) {
      toaster.push('Already adding — Enter to keep it, Esc to discard.')
      return
    }
    const at = deck?.currentMs() ?? rally?.start_ms ?? 0
    addAnchorMs = Math.round(at)
    draft = draftSpanAt(at, source.duration_ms)
  }

  function cancelAdd(): void {
    draft = null
    toaster.push('Discarded — nothing was added.')
  }

  /** `[` and `]`, while an add is open. Refusals are surfaced for the same
   *  reason applyEdit surfaces them: a key that silently does nothing is
   *  indistinguishable from one that is broken. */
  function applyDraftEdit(edit: BoundsEdit): void {
    if (!edit.ok) {
      toaster.push(edit.reason)
      return
    }
    draft = { startMs: edit.startMs, endMs: edit.endMs }
  }

  /**
   * `Enter`: persist the draft as a rally.
   *
   * The list is updated in place (applyAdd) rather than by asking Session
   * to remount, for the reason splitHere gives: a remount resets the
   * playhead, and the reviewer's next move after adding a span is almost
   * always to trim it. `currentId` lands on the new rally so that trim is
   * one keypress away -- the same courtesy mergeBack pays by landing on
   * the survivor.
   *
   * No clip job is queued here and none is queued on the server. Adding a
   * span never starts an encode; cutting stays the explicit button.
   */
  async function commitAdd(): Promise<void> {
    if (!draft || !source) return
    const span = draft
    try {
      const id = await api.createRally(source.id, span.startMs, span.endMs)
      rallies = applyAdd(
        rallies,
        {
          id,
          sessionId: detail.session.id,
          sourceId: source.id,
          startMs: span.startMs,
          endMs: span.endMs,
        },
        sourceOrder,
      )
      draft = null
      currentId = id
      markSaved()
      toaster.push(`Added a rally — ${formatTs(span.startMs)} to ${formatTs(span.endMs)}`)
    } catch (e) {
      console.error('failed to add rally', e)
      saveState = 'error'
      // The draft is deliberately left open: it is the only copy of a span
      // the reviewer picked by eye, and clearing it on a failed write would
      // make them find it again.
      toaster.push('Could not add this rally — check that the server is running.')
    }
  }

  function onKey(e: KeyboardEvent) {
    if (isEditableTarget(e.target)) return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if (!rally || !source) return
    // An open add owns the keyboard. `[`, `]`, Esc and the playback keys
    // all mean something here, but they mean it about the DRAFT -- so they
    // are dispatched from one place that knows which of the two is being
    // edited, rather than from handlers each re-deriving it. The keys that
    // have no draft reading (`C`, `U`) refuse out loud instead of going
    // dead: a key that silently does nothing reads as a broken app, which
    // is the same argument applyEdit's refusals are built on.
    const d = draft
    switch (e.key) {
      case 'Escape':
        if (d) cancelAdd()
        else onclose()
        break
      case 'Enter':
        if (d) commitAdd()
        break
      case 'n':
      case 'N':
        startAdd()
        break
      case '[':
        if (d) applyDraftEdit(setDraftIn(d, deck?.currentMs() ?? d.startMs, source.duration_ms))
        else applyEdit(setInPoint(rally.start_ms, rally.end_ms, deck?.currentMs() ?? rally.start_ms))
        break
      case ']':
        if (d) applyDraftEdit(setDraftOut(d, deck?.currentMs() ?? d.endMs, source.duration_ms))
        else applyEdit(setOutPoint(rally.start_ms, rally.end_ms, deck?.currentMs() ?? rally.end_ms))
        break
      case 'c':
      case 'C':
        if (d) toaster.push('Finish the add first — Enter to keep it, Esc to discard.')
        else splitHere()
        break
      case 'u':
      case 'U':
        if (d) toaster.push('Finish the add first — Enter to keep it, Esc to discard.')
        else mergeBack()
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
  <!-- Capped so the deck, the status line, both bands and the threshold row
       share one screen: trimming is a deck-and-band gesture and scrolling
       between them is the failure this removes. 28rem is what sits below
       the deck plus the page header; a 16:9 frame at (100vh - 28rem) tall
       is this wide. On a tall display the cap is never reached. -->
  <div class="mx-auto w-full max-w-[calc((100vh-28rem)*16/9)]">
    <!-- While an add is open the deck spans the rest of the FILE, not the
         rally: its out-point is what pauses playback, and watching footage
         the detector proposed nothing for is the entire point of the mode.
         The in-point is the playhead at the moment `N` was pressed (see
         addAnchorMs) rather than 0, so the re-seek VideoDeck performs on a
         startMs change lands where the playhead already was. -->
    <VideoDeck
      bind:this={deck}
      src={api.proxyUrl(detail.session.id, source.idx)}
      startMs={draft ? addAnchorMs : rally.start_ms}
      endMs={draft ? source.duration_ms : rally.end_ms}
      onended={() => writePlayhead(draft ? source.duration_ms : rally.end_ms)}
      onprogress={() => writePlayhead(deck?.currentMs() ?? 0)}
    />
  </div>

  <p class="mt-2 font-data text-data text-dim">
    rally {rally.idx} · {formatTs(rally.start_ms)} → {formatTs(rally.end_ms)}
  </p>

  {#if draft}
    <!-- The one surface in the app that says a rally is being made rather
         than edited. It is an outlined card, not a coloured one: there is
         no accent, and state is carried by fill, outline and weight. -->
    <section class="mt-4 space-y-2 rounded border border-fg bg-surface p-3">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <p class="text-body text-fg">Adding a rally the detector missed</p>
        <p class="font-data text-data text-fg">
          {formatTs(draft.startMs)} → {formatTs(draft.endMs)} ·
          {((draft.endMs - draft.startMs) / 1000).toFixed(1)}s
        </p>
      </div>
      <SourceScrub
        bind:this={sourceScrub}
        durationMs={source.duration_ms}
        draft={draft}
        rallies={rallies.filter((r) => r.source_id === source.id)}
        onscrub={(ms) => {
          deck?.seekTo(ms)
          writePlayhead(ms)
        }}
      />
      <p class="text-caption text-dim">
        Click the bar to move the playhead, then [ and ] to set the ends —
        Enter keeps it, Esc discards it. Nothing is cut until you ask for it.
      </p>
    </section>
  {/if}

  <section class="mt-4 space-y-1">
    <p class="text-caption tracking-wide text-faint uppercase">whole session</p>
    <OverviewBand
      {rallies}
      sources={detail.sources}
      currentId={rally.id}
      windowStartMs={toSessionMs(detail.sources, rally.source_id, effectiveWin.startMs)}
      windowEndMs={toSessionMs(detail.sources, rally.source_id, effectiveWin.endMs)}
      onpick={(id) => (currentId = id)}
      rules={detail.session.scoring}
    />
  </section>

  <section class="mt-4 space-y-1">
    <p class="text-caption tracking-wide text-faint uppercase">±20s — drag either handle</p>
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
      <span class="text-caption text-faint">preview threshold</span>
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
      <span class="w-10 font-data text-caption text-faint">
        {threshold === null ? '…' : threshold.toFixed(2)}
      </span>
    </div>

    <p class="flex items-center gap-2 font-data text-caption text-faint">
      <span>detector score — dashed line is the threshold</span>
      <!-- Edits persist on drag-release and on [ / ], with no save button, so
           this is the only thing telling the user an edit took. -->
      {#if saveState === 'saved'}
        <span class="text-faint" role="status">✓ saved</span>
      {:else if saveState === 'error'}
        <span class="text-danger" role="status">✕ not saved</span>
      {/if}
    </p>

    <KeyHints mode="timeline" />
  </section>

  {#if toaster.toasts.length > 0}
    <div class="pointer-events-none fixed bottom-4 left-1/2 z-50 -translate-x-1/2 space-y-2">
      {#each toaster.toasts as t (t.id)}
        <div class="rounded {toastToneClasses(t.tone, 'muted')} px-3 py-2 text-body shadow-lg">
          {t.message}
        </div>
      {/each}
    </div>
  {/if}
{:else}
  <p class="text-body text-dim">No rallies to show.</p>
{/if}
