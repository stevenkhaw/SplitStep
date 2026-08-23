<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { describeExportResult, exportSetLabel } from '../lib/export'
  import { flashFor } from '../lib/flash'
  import { isEditableTarget } from '../lib/keyboard'
  import { NOTE_MAX_CHARS, NoteWriter, seedNotes } from '../lib/notes'
  import { describePersistFailure, persistAction } from '../lib/persist'
  import { QueueController } from '../lib/queue'
  import { navigate } from '../lib/router.svelte'
  import { fractionToScrubMs, scrubMsToFraction } from '../lib/scrub'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import { formatDuration, formatTs } from '../lib/time'
  import type { VerdictFlash } from '../lib/flash'
  import type { QueueAction } from '../lib/queue'
  import type { Rally, SessionDetail, Source } from '../lib/types'
  import KeyHints from './KeyHints.svelte'
  import VideoDeck from './VideoDeck.svelte'

  interface Props {
    detail: SessionDetail
    /** `liveRallies` is this session's live-merged snapshot (see
     * QueueController.liveSnapshot) -- passed along so TimelineMode's
     * OverviewBand can color a rally starred/rejected earlier in this queue
     * session correctly, instead of seeding from `detail.rallies`' stale
     * server-snapshot flags. */
    onopen_timeline: (rallyId: string, liveRallies: Rally[]) => void
    /** Enter label mode, optionally on this rally -- Session threads it
     * through as startAtRallyId the same way onopen_timeline's rallyId is,
     * so a fresh QueueController on return jumps back here instead of
     * opening on whichever rally is first-unreviewed. `null` when `current`
     * is undefined (the pass is finished): unlike `t`/timeline, label mode
     * needs no specific rally to open on, since LabelController iterates the
     * full unfiltered rally list rather than following the queue's cursor --
     * and "just finished reviewing" is exactly when a reviewer is most
     * likely to want it. Separate from review: a verdict is a note about the
     * detector, not a decision about the clip, so it deliberately does not
     * touch star/reject or the session's review status. */
    onopen_label: (rallyId: string | null) => void
    /** Open on this rally instead of the first unreviewed one, when it is
     * still in the queue. Session passes the rally the user just left the
     * timeline from -- see the constructor call below for why a remount
     * would otherwise lose their place. */
    startAtRallyId?: string | null
  }

  let { detail, onopen_timeline, onopen_label, startAtRallyId = null }: Props = $props()

  // Deliberately a one-time snapshot, not a reactive read: the queue state
  // machine is constructed once per mounted QueueMode and owns its own
  // index/star/reject state thereafter. Session.svelte guarantees this is
  // safe -- not merely by convention but structurally -- by wrapping the
  // mode components in `{#key detail.rallies}`: any replacement of `detail`
  // (e.g. Task 13's re-segment) changes that key and remounts QueueMode
  // fresh, so a mounted instance can never observe `detail` changing out
  // from under it. `untrack` tells svelte-check this one-time read is
  // intentional rather than an accidental non-reactive reference.
  const queue = new QueueController(untrack(() => detail.rallies))
  // A fresh controller opens on the first rally whose seen_at is null,
  // which is the right resume point for a new session and the wrong one for
  // a remount. Returning from the timeline forces a remount (Session bumps
  // rallyRevision -- the only way a trimmed rally's new bounds reach the
  // queue at all), and seen_at is stamped by star/point/reject/skip alone,
  // never by a bounds edit, so trimming rally 40 of 61 and pressing esc
  // would drop the user back at whichever rally they had not yet seen.
  //
  // jumpTo silently does nothing for an id it cannot find, which is exactly
  // right for the other remount trigger: a re-segment rebuilds every rally
  // with a new uuid, so a stale id from a previous timeline visit correctly
  // falls through to the first-unreviewed default.
  const initialRallyId = untrack(() => startAtRallyId)
  if (initialRallyId) queue.jumpTo(initialRallyId)
  const toaster = createToaster()

  // Reject takes the receding tone here too, not danger.
  const FLASH_TONE = {
    star: 'text-star',
    point: 'text-point',
    reject: 'text-faint',
    neutral: 'text-dim',
  } as const

  let version = $state(0) // bumped to re-read the controller after a mutation
  let speed = $state(1)
  let deck = $state<VideoDeck>()
  let progressBar = $state<HTMLDivElement>()
  let scrubTrack = $state<HTMLDivElement>()
  // Plain let, not $state: only the pointer handlers read it, and a drag
  // that re-rendered the panel on every pointermove is exactly what the
  // direct-to-DOM painting below exists to avoid.
  let scrubbing = false

  // NoteWriter (lib/notes.ts) owns the id -> note map, the optimistic set,
  // the POST and the failure restore -- a plain class, like QueueController,
  // so it needs the same `version`-style bump below to make its mutations
  // visible to Svelte. Notes stay out of QueueController itself: that models
  // three booleans with an undo stack, and a note is free text with no
  // toggle semantics that an undo history for verdicts has no business
  // carrying.
  const noteWriter = new NoteWriter(api, seedNotes(untrack(() => detail.rallies)))
  let notesVersion = $state(0)
  let editingNote = $state(false)
  let noteBuffer = $state('')
  let noteInput = $state<HTMLInputElement>()

  // `version` is the dependency that forces a re-read after a mutation --
  // QueueController is a plain class, so Svelte cannot track it directly:
  // reading a plain getter registers no signal, so an expression that reads
  // only `queue.whatever` would render once and then freeze. Every value
  // below that comes from the controller explicitly reads `version` (and,
  // where relevant, `speed`) so it recomputes on every mutation.
  const current = $derived.by(() => {
    version
    return queue.current
  })
  const next = $derived.by(() => {
    version
    return queue.nextRally
  })
  // Read via isStarred/isRejected (never `current.starred`/`.rejected` --
  // those fields on the Rally the controller was constructed with are
  // deliberately never mutated, so they would render whatever the server
  // last saw, not this session's live toggles) so the indicator reflects
  // the true live state after a star/reject/back sequence.
  const currentStarred = $derived.by(() => {
    version
    return queue.currentIsStarred
  })
  const currentRejected = $derived.by(() => {
    version
    return queue.currentIsRejected
  })
  const currentPoint = $derived.by(() => {
    version
    return queue.currentIsPoint
  })
  // notesVersion is NoteWriter's equivalent of `version` above: a plain
  // class's mutations register no Svelte signal on their own, so the ✎
  // indicator needs an explicit dependency to re-read has()/get() by.
  const currentNote = $derived.by(() => {
    notesVersion
    return current ? noteWriter.get(current.id) : ''
  })
  const currentHasNote = $derived.by(() => {
    notesVersion
    return current ? noteWriter.has(current.id) : false
  })
  const stats = $derived.by(() => {
    version
    return {
      index: queue.index,
      total: queue.total,
      starredCount: queue.starredCount,
      rejectedCount: queue.rejectedCount,
      pointCount: queue.pointCount,
      remainingMs: queue.remainingMs(speed),
    }
  })

  function sourceFor(sourceId: string): Source | undefined {
    return detail.sources.find((s) => s.id === sourceId)
  }

  function srcFor(sourceId: string): string {
    const s = sourceFor(sourceId)
    return s ? api.proxyUrl(detail.session.id, s.idx) : ''
  }

  // The rally's 1-based, session-wide position (Rally.idx from the server),
  // for a human-readable toast -- stable regardless of where the queue's
  // cursor has since moved on to.
  function rallyIdx(rallyId: string): number | undefined {
    return detail.rallies.find((r) => r.id === rallyId)?.idx
  }

  // The confirmation shown over the video. `seq` is what re-triggers the CSS
  // animation on a repeat of the same verdict -- keying only on the flash
  // object would leave a second identical press silent, and holding X down
  // through a run of false positives is exactly when the feedback matters.
  let flash = $state<VerdictFlash | null>(null)
  let flashSeq = $state(0)
  let flashTimer: ReturnType<typeof setTimeout> | undefined

  function showFlash(action: QueueAction): void {
    const next = flashFor(action)
    if (!next) return
    flash = next
    flashSeq += 1
    clearTimeout(flashTimer)
    flashTimer = setTimeout(() => (flash = null), 700)
  }

  $effect(() => () => clearTimeout(flashTimer))

  async function apply(action: QueueAction | null): Promise<void> {
    version += 1
    if (!action) return
    // Before the await, not after: the point is to confirm the keypress at
    // the moment it lands. persistAction is a round trip, and a failure
    // still surfaces through the toaster below.
    showFlash(action)
    const outcome = await persistAction(action, api)
    if (!outcome.ok) {
      // A failed persist must not silently diverge local state from the
      // server. For a PersistableAction (star/reject/skip), revert() undoes
      // exactly this rally's flags without touching the index or the undo
      // stack -- unlike undo(), which is user-facing, pops LIFO, and would
      // walk back whichever action happens to be on top rather than the one
      // that actually failed. An UndoAction has nothing to revert to
      // (outcome.revert is null), so it can only be surfaced.
      //
      // The queue itself is never halted here -- this runs on a LAN box, a
      // failure means the server died (which the jobs badge already
      // surfaces), and stopping the review flow over a rare event punishes
      // the user for it.
      if (outcome.revert) {
        queue.revert(outcome.revert)
        version += 1
      }
      toaster.push(describePersistFailure(action, rallyIdx(action.rallyId)))
    }
  }

  // Cutting is fire-and-forget from here: the jobs badge already shows
  // encode progress, so this only needs to report what plan_export decided
  // -- queued vs. the three reasons the rest were not -- through the same
  // toaster star/reject failures use, so a second press mid-encode reads as
  // honest progress rather than a dead button.
  async function exportSet(which: 'points' | 'starred'): Promise<void> {
    try {
      const result = await api.exportClips(detail.session.id, which)
      // 'info': this is the plan's outcome, not a failure -- a queued/
      // already-cut/in-flight/unavailable breakdown is the primary success
      // feedback for the export feature, and rendering it red would read as
      // the request having failed when it did exactly what was asked.
      toaster.push(describeExportResult(which, result), 'info')
    } catch (e) {
      toaster.push(`Couldn't export ${exportSetLabel(which)} -- ${String(e)}`)
    }
  }

  // Creating a reel cuts NOTHING. §6.2 draws that line deliberately: the
  // builder's "Cut missing clips" stays the only path that starts an
  // encode, so a button labelled "reel" never silently launches half an
  // hour of work. A second press merges additively server-side, so pressing
  // this again after marking three more points appends those three and
  // leaves any hand-ordering alone.
  async function buildReel(which: 'points' | 'starred'): Promise<void> {
    try {
      const result = await api.createSessionReel(detail.session.id, which)
      navigate(`/reels/${result.slug}`)
    } catch (e) {
      toaster.push(`Couldn't build the ${exportSetLabel(which)} reel -- ${String(e)}`)
    }
  }

  function openNote() {
    if (!current) return
    noteBuffer = noteWriter.get(current.id)
    editingNote = true
    // Focus after the field exists. Svelte renders on the microtask queue, so
    // the element is not in the DOM at the point this handler returns.
    queueMicrotask(() => noteInput?.select())
  }

  // Thin orchestration only -- the optimistic set, the POST and the
  // failure-restore all live in NoteWriter.commit (lib/notes.ts) so they are
  // unit-tested; jsdom has no <video>, so this component itself can only be
  // verified by hand.
  async function commitNote() {
    // Guards against a second call: the field's onblur also targets this
    // function, and removing a focused element from the DOM fires blur on
    // it -- so both an Enter and an Escape already close the field
    // (editingNote = false) themselves, and that same removal then blurs
    // the input a moment later. Without this guard, that trailing blur
    // would re-run commitNote a second time: after Enter, a pointless
    // duplicate POST of the same value; after Escape, a real one -- silently
    // saving the exact text the reviewer just discarded.
    if (!editingNote) return
    const rally = current
    if (!rally) return
    const buffer = noteBuffer
    editingNote = false
    // commit() applies its optimistic map update synchronously, before
    // returning -- bumping notesVersion right away (rather than after the
    // await below) is what makes the ✎ indicator update immediately instead
    // of waiting on the round trip, matching how star/point/reject already
    // feel on a LAN box.
    const pending = noteWriter.commit(rally.id, buffer)
    notesVersion += 1
    const outcome = await pending
    if (outcome.status === 'failed') {
      // Put back exactly what the server last accepted -- NoteWriter already
      // did that internally, this just re-renders to show it. Unlike a
      // failed star there is no revert action to hand the controller: notes
      // are not part of the undo stack.
      notesVersion += 1
      toaster.push(`Couldn't save the note -- ${String(outcome.error)}`)
    }
  }

  function onNoteKey(e: KeyboardEvent) {
    // Handled on the field itself rather than in onKey: these two keys mean
    // commit and cancel only while the field is open, and giving them a
    // second global meaning would make them depend on what has focus.
    if (e.key === 'Enter') {
      e.preventDefault()
      commitNote()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      // Cancel, not commit: Escape discards, which is why noteBuffer is
      // never written back to noteWriter here.
      editingNote = false
    }
  }

  function onProgress(fraction: number) {
    // Written straight to the DOM. Routing a 60Hz update through Svelte state
    // would re-render the whole panel on every frame.
    paintScrub(fraction)
  }

  function paintScrub(fraction: number) {
    if (progressBar) progressBar.style.transform = `scaleX(${fraction})`
  }

  function scrubFraction(e: PointerEvent): number {
    if (!scrubTrack) return 0
    const rect = scrubTrack.getBoundingClientRect()
    return (e.clientX - rect.left) / rect.width
  }

  // Shared by click-to-seek and every pointermove of a drag, mirroring label
  // mode's bar. Seek first, then paint immediately instead of waiting for
  // VideoDeck's next onprogress tick -- that is ~16ms away, which a drag
  // (firing far faster) reads as lag on the one control the hand is on.
  //
  // Scrubbing deliberately does not pause: the clip keeps running under the
  // playhead, so releasing mid-rally leaves queue mode in the state it was
  // already in rather than needing a resume rule.
  function seekToFraction(fraction: number) {
    if (!current) return
    // Clamped inside the rally's span by fractionToScrubMs. Overshooting the
    // track is routine once the pointer has capture, and an unclamped seek
    // would put the next rally's footage on screen -- the exact thing
    // VideoDeck's out-point guard exists to prevent.
    const ms = fractionToScrubMs(fraction, current.start_ms, current.end_ms)
    deck?.seekTo(ms)
    paintScrub(scrubMsToFraction(ms, current.start_ms, current.end_ms))
  }

  function onScrubDown(e: PointerEvent) {
    if (!scrubTrack) return
    scrubbing = true
    scrubTrack.setPointerCapture(e.pointerId)
    seekToFraction(scrubFraction(e))
  }

  function onScrubMove(e: PointerEvent) {
    if (!scrubbing) return
    seekToFraction(scrubFraction(e))
  }

  function endScrub(e: PointerEvent) {
    scrubbing = false
    scrubTrack?.releasePointerCapture(e.pointerId)
  }

  function onBlocked() {
    // VideoDeck already renders its own click-to-play overlay for a blocked
    // autoplay -- queue mode has nothing to add on top of that today. The
    // handler is still wired up so a future global notice has a hook.
  }

  function onKey(e: KeyboardEvent) {
    if (isEditableTarget(e.target)) return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    switch (e.key) {
      case 's':
      case 'S':
        apply(queue.star())
        break
      case 'x':
      case 'X':
        apply(queue.reject())
        break
      case 'p':
      case 'P':
        apply(queue.point())
        break
      case 'r':
      case 'R':
        deck?.replay()
        break
      case 'ArrowRight':
        e.preventDefault()
        apply(queue.skip())
        break
      case 'ArrowLeft':
        e.preventDefault()
        queue.back()
        version += 1
        break
      case 'u':
      case 'U':
        apply(queue.undo())
        break
      case '`':
      case '~':
        // Backtick sits left of 1, so the speed row reads ` 1 2 3 in
        // ascending order under the fingers. '~' is the same physical key
        // with shift held -- a slip there should still slow down rather
        // than do nothing.
        speed = 0.5
        break
      case '1':
        speed = 1
        break
      case '2':
        speed = 1.5
        break
      case '3':
        speed = 2
        break
      case ' ':
        e.preventDefault()
        if (deck?.paused()) deck?.play()
        else deck?.pause()
        break
      case 't':
      case 'T':
        if (current) onopen_timeline(current.id, queue.liveSnapshot(detail.rallies))
        break
      case 'n':
      case 'N':
        e.preventDefault()
        openNote()
        break
      case 'l':
      case 'L':
        // No `if (current)` guard here, unlike 't' -- see the onopen_label
        // doc comment above for why label mode has no need of one.
        onopen_label(current ? current.id : null)
        break
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#if stats.total === 0}
  <!-- Finding 5: zero rallies and "finished reviewing" are otherwise
       indistinguishable (`new QueueController([]).current` is undefined
       either way). Naming the actual cause here -- nothing detected yet, or
       the threshold produced none -- points at what to do next instead of
       misreporting a session that was never reviewed as reviewed. -->
  <section class="rounded-lg border border-line p-8 text-center">
    <h2 class="text-title font-semibold">No rallies to review</h2>
    <p class="mt-2 font-data text-body text-dim">
      Nothing has been detected for this session yet, or the current threshold produced zero
      rallies. Re-segment at a lower threshold below, or wait for detection to finish.
    </p>
  </section>
{:else if !current}
  <section class="rounded-lg border border-line p-8 text-center">
    <h2 class="text-title font-semibold">Session reviewed</h2>
    <p class="mt-2 font-data text-body text-dim">
      {stats.total} seen · ★{stats.starredCount} starred · ✕{stats.rejectedCount} rejected
    </p>
    <!-- M3: this screen used to render no help line at all, so the only way
         into label mode -- pressing L -- was undiscoverable exactly when a
         reviewer who just finished a pass is most likely to want it. -->
    <p class="mt-4 font-data text-data text-faint">L label</p>
    <!-- Two rows, four actions: cut, and compile. The reel buttons are
         ADDITIONS beside Plan A's export pair, never replacements -- cutting
         clips and compiling a reel are different decisions, and only the
         first one starts an encode. -->
    <div class="mt-4 flex items-center justify-center gap-3">
      <button
        class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
               hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
        disabled={stats.pointCount === 0}
        onclick={() => exportSet('points')}
      >
        Export point clips ({stats.pointCount})
      </button>
      <button
        class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
               hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
        disabled={stats.starredCount === 0}
        onclick={() => exportSet('starred')}
      >
        Export starred clips ({stats.starredCount})
      </button>
    </div>
    <div class="mt-2 flex items-center justify-center gap-3">
      <button
        class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
               hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
        disabled={stats.pointCount === 0}
        onclick={() => buildReel('points')}
      >
        Reel of all points ({stats.pointCount})
      </button>
      <button
        class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
               hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
        disabled={stats.starredCount === 0}
        onclick={() => buildReel('starred')}
      >
        Reel of starred ({stats.starredCount})
      </button>
    </div>
  </section>
{:else}
  <!-- `relative` so the position counter can sit over the video. The counter
       duplicates the "rally N / M" in the metadata line below on purpose: while
       a clip is playing your eyes are on the video, and looking away to find
       your place in a 61-rally pass is the thing this removes. -->
  <div class="relative">
    <VideoDeck
      bind:this={deck}
      src={srcFor(current.source_id)}
      startMs={current.start_ms}
      endMs={current.end_ms}
      nextSrc={next ? srcFor(next.source_id) : undefined}
      nextStartMs={next?.start_ms}
      {speed}
      onended={() => deck?.replay()}
      onprogress={onProgress}
      onblocked={onBlocked}
    />
    <div
      class="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-1
             font-data text-data tabular-nums text-fg"
    >
      {stats.index + 1} / {stats.total}
    </div>

    {#if flash}
      <!-- Centred over the video rather than in the status line, because
           that is where the eye already is during a pass. aria-live so the
           confirmation is not purely visual; pointer-events-none so it can
           never swallow a click meant for the deck. -->
      {#key flashSeq}
        <div
          class="pointer-events-none absolute inset-0 flex items-center justify-center"
          role="status"
          aria-live="polite"
        >
          <span
            class="flex items-center gap-2 rounded-full bg-black/70 px-4 py-2 font-data text-data
                   motion-safe:animate-[verdict_700ms_ease-out_forwards]
                   {FLASH_TONE[flash.tone]}"
          >
            <span aria-hidden="true">{flash.glyph}</span>
            {flash.label}
          </span>
        </div>
      {/key}
    {/if}
  </div>

  <!--
    Taller than the h-1 indicator this replaced, and cursor-ew-resize, because
    it now has to read as draggable at a glance. pointerdown/pointermove drive
    both click-to-seek and the drag rather than onclick -- matching label mode
    and ZoomBand, and it is what lets a plain tabindex="0" + role="slider"
    satisfy svelte-check's a11y rules with no keyboard handler, since
    click-events-have-key-events only fires for onclick.

    No arrow-key seeking here on purpose: ArrowLeft/Right are back/next rally
    in queue mode, and a second meaning that depended on which element had
    focus is worse than none.
  -->
  <div
    bind:this={scrubTrack}
    class="relative mt-3 h-2 cursor-ew-resize overflow-hidden rounded bg-surface-2"
    onpointerdown={onScrubDown}
    onpointermove={onScrubMove}
    onpointerup={endScrub}
    onpointercancel={endScrub}
    role="slider"
    tabindex="0"
    aria-label="scrub within rally"
    aria-valuemin={current.start_ms}
    aria-valuemax={current.end_ms}
    aria-valuenow={current.start_ms}
  >
    <div
      bind:this={progressBar}
      class="h-full origin-left rounded bg-accent"
      style="transform: scaleX(0)"
    ></div>
  </div>

  {#if editingNote}
    <!-- One line, and no textarea: the caption renders as at most two lines at
         4K, so a field that invites a paragraph would invite text the export
         has to truncate. Enter/Escape are handled on the field itself (see
         onNoteKey); the queue's window handler already stands down for an
         editable target, which editable-target-guard.test.ts pins. -->
    <input
      bind:this={noteInput}
      bind:value={noteBuffer}
      onkeydown={onNoteKey}
      onblur={commitNote}
      maxlength={NOTE_MAX_CHARS}
      placeholder="note for this rally — Enter saves, Esc cancels"
      aria-label="rally note"
      class="mt-2 w-full rounded border border-line bg-surface px-2 py-1
             font-data text-body"
    />
  {/if}

  <!-- Two groups, and they mean different things: the left is *this rally's*
       state, the right is the session tally. `★` previously appeared in both
       halves of one undivided line with no way to tell which was which. The
       left group is now boxed toggles that fill when set, because the old
       treatment drew the unset state in text-neutral-700 -- close enough to
       the background that "not starred" and "no such control" looked the
       same. -->
  <div class="mt-2 flex items-center justify-between gap-4 font-data text-data text-dim">
    <span class="flex items-center gap-3">
      <span class="flex items-center gap-1.5">
        <span
          class="grid h-[22px] w-[22px] place-items-center rounded border text-caption leading-none
                 {currentStarred
            ? 'border-star/35 bg-star/15 text-star'
            : 'border-transparent bg-surface-2 text-faint'}"
          title={currentStarred ? 'starred' : 'not starred'}
        >★</span>
        <span
          class="grid h-[22px] w-[22px] place-items-center rounded border text-caption leading-none
                 {currentPoint
            ? 'border-point/35 bg-point/15 text-point'
            : 'border-transparent bg-surface-2 text-faint'}"
          title={currentPoint ? 'point' : 'not a point'}
        >●</span>
        <span
          class="grid h-[22px] w-[22px] place-items-center rounded border text-caption leading-none
                 {currentHasNote
            ? 'border-accent/35 bg-accent/15 text-accent'
            : 'border-transparent bg-surface-2 text-faint'}"
          title={currentHasNote ? currentNote : 'no note'}
        >✎</span>
      </span>
      <span class="text-fg">rally {stats.index + 1} / {stats.total}</span>
      <span>{formatTs(current.start_ms)} · {formatDuration(current.end_ms - current.start_ms)}</span>
      <!-- Rejected is deliberately not danger-coloured. Detection is
           recall-biased, so rejecting is the most frequent action here; red
           would state "error" about the routine case. It recedes instead. -->
      {#if currentRejected}<span class="text-faint">rejected</span>{/if}
    </span>
    <span class="flex shrink-0 items-center gap-3">
      <span class="text-star">★ {stats.starredCount}</span>
      <span class="text-point">● {stats.pointCount}</span>
      <span class="text-faint">✕ {stats.rejectedCount}</span>
      <span>~{formatDuration(stats.remainingMs)} left at {speed}×</span>
    </span>
  </div>

  <KeyHints mode="queue" />
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
