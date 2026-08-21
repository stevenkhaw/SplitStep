<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { isEditableTarget } from '../lib/keyboard'
  import { describePersistFailure, persistAction } from '../lib/persist'
  import { QueueController } from '../lib/queue'
  import { createToaster } from '../lib/toaster.svelte'
  import { formatDuration, formatTs } from '../lib/time'
  import type { QueueAction } from '../lib/queue'
  import type { Rally, SessionDetail, Source } from '../lib/types'
  import VideoDeck from './VideoDeck.svelte'

  interface Props {
    detail: SessionDetail
    /** `liveRallies` is this session's live-merged snapshot (see
     * QueueController.liveSnapshot) -- passed along so TimelineMode's
     * OverviewBand can color a rally starred/rejected earlier in this queue
     * session correctly, instead of seeding from `detail.rallies`' stale
     * server-snapshot flags. */
    onopen_timeline: (rallyId: string, liveRallies: Rally[]) => void
    /** Open on this rally instead of the first unreviewed one, when it is
     * still in the queue. Session passes the rally the user just left the
     * timeline from -- see the constructor call below for why a remount
     * would otherwise lose their place. */
    startAtRallyId?: string | null
  }

  let { detail, onopen_timeline, startAtRallyId = null }: Props = $props()

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
  // A fresh controller opens on the first rally whose reviewed_at is null,
  // which is the right resume point for a new session and the wrong one for
  // a remount. Returning from the timeline forces a remount (Session bumps
  // rallyRevision -- the only way a trimmed rally's new bounds reach the
  // queue at all), and reviewed_at is stamped by star/reject alone, never by
  // a bounds edit, so trimming rally 40 of 61 and pressing esc would drop
  // the user back at whichever rally they had not yet judged.
  //
  // jumpTo silently does nothing for an id it cannot find, which is exactly
  // right for the other remount trigger: a re-segment rebuilds every rally
  // with a new uuid, so a stale id from a previous timeline visit correctly
  // falls through to the first-unreviewed default.
  const initialRallyId = untrack(() => startAtRallyId)
  if (initialRallyId) queue.jumpTo(initialRallyId)
  const toaster = createToaster()
  let version = $state(0) // bumped to re-read the controller after a mutation
  let speed = $state(1)
  let deck = $state<VideoDeck>()
  let progressBar = $state<HTMLDivElement>()

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
  const stats = $derived.by(() => {
    version
    return {
      index: queue.index,
      total: queue.total,
      starredCount: queue.starredCount,
      rejectedCount: queue.rejectedCount,
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

  async function apply(action: QueueAction | null): Promise<void> {
    version += 1
    if (!action) return
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

  function onProgress(fraction: number) {
    // Written straight to the DOM. Routing a 60Hz update through Svelte state
    // would re-render the whole panel on every frame.
    if (progressBar) progressBar.style.transform = `scaleX(${fraction})`
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
  <section class="rounded-lg border border-neutral-800 p-8 text-center">
    <h2 class="text-lg font-semibold">No rallies to review</h2>
    <p class="mt-2 font-mono text-sm text-neutral-400">
      Nothing has been detected for this session yet, or the current threshold produced zero
      rallies. Re-segment at a lower threshold below, or wait for detection to finish.
    </p>
  </section>
{:else if !current}
  <section class="rounded-lg border border-neutral-800 p-8 text-center">
    <h2 class="text-lg font-semibold">Session reviewed</h2>
    <p class="mt-2 font-mono text-sm text-neutral-400">
      {stats.total} seen · ★{stats.starredCount} starred · ✕{stats.rejectedCount} rejected
    </p>
    <!-- Spec 6 also puts "Export starred clips (4K)" and "Add all starred to a
         reel" here. Both need clip export, which is Plan 3. -->
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
             font-mono text-xs tabular-nums text-neutral-200"
    >
      {stats.index + 1} / {stats.total}
    </div>
  </div>

  <div class="mt-3 h-1 overflow-hidden rounded bg-neutral-800">
    <div
      bind:this={progressBar}
      class="h-full origin-left bg-blue-500"
      style="transform: scaleX(0)"
    ></div>
  </div>

  <div class="mt-2 flex items-center justify-between font-mono text-xs text-neutral-400">
    <span class="flex items-center gap-2">
      <span
        class="text-base leading-none {currentStarred ? 'text-yellow-400' : 'text-neutral-700'}"
        title={currentStarred ? 'starred' : 'not starred'}
      >★</span>
      rally {stats.index + 1} / {stats.total} ·
      {formatTs(current.start_ms)} · {formatDuration(current.end_ms - current.start_ms)}
      {#if currentRejected}<span class="text-red-400">· rejected</span>{/if}
    </span>
    <span>
      ★{stats.starredCount} ✕{stats.rejectedCount} ·
      ~{formatDuration(stats.remainingMs)} left at {speed}×
    </span>
  </div>

  <p class="mt-4 font-mono text-xs text-neutral-500">
    S star · X reject (again to undo) · R replay · ← back · → next · U undo · 1/2/3 speed · T timeline
  </p>
{/if}

{#if toaster.toasts.length > 0}
  <div class="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
    {#each toaster.toasts as t (t.id)}
      <div class="rounded bg-red-500/90 px-3 py-2 text-sm text-white shadow-lg">
        {t.message}
      </div>
    {/each}
  </div>
{/if}
