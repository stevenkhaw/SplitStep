<script lang="ts">
  import ErrorNote from '../components/ErrorNote.svelte'
  import JobsBadge from '../components/JobsBadge.svelte'
  import LabelMode from '../components/LabelMode.svelte'
  import QueueMode from '../components/QueueMode.svelte'
  import QuadEditor from '../components/QuadEditor.svelte'
  import ResegmentPanel from '../components/ResegmentPanel.svelte'
  import TimelineMode from '../components/TimelineMode.svelte'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import type { Rally, SessionDetail, Source } from '../lib/types'

  interface Props {
    id: string
  }
  let { id }: Props = $props()

  let detail = $state<SessionDetail | null>(null)
  let error = $state<unknown>(null)
  let mode = $state<'queue' | 'timeline' | 'label'>('queue')
  let focusedRallyId = $state<string | null>(null)
  // The live-merged rallies QueueMode hands to openTimeline (see
  // QueueController.liveSnapshot) -- threaded through so TimelineMode's
  // OverviewBand can color a rally starred/rejected earlier in this queue
  // session correctly. Falls back to `detail.rallies` inside TimelineMode
  // itself when null (its own default), so this only needs to be set, never
  // explicitly cleared.
  let timelineRallies = $state<Rally[] | null>(null)

  // Bumped only when the rally *set* actually needs QueueMode/TimelineMode
  // to remount: the initial load (or a navigation to a different session
  // entirely, below), re-segmentation (a different id/count), or a bounds
  // edit made in TimelineMode (same ids, but QueueController/TimelineMode
  // each hold their own frozen copy of start_ms/end_ms that a remount is
  // what refreshes). QuadEditor's onassigned refetch deliberately does NOT
  // bump this: assigning a play-region preset never changes rally content,
  // so forcing a remount there would only needlessly discard QueueMode's
  // undo stack for a change it has nothing to do with (Finding 6,
  // "QuadEditor's onassigned has the same shape"). See the `{#key}` below
  // for where this is consumed.
  let rallyRevision = $state(0)

  const needsSetupSources = $derived(detail?.sources.filter((s) => s.status === 'needs_setup') ?? [])
  const readySources = $derived(detail?.sources.filter((s) => s.status !== 'needs_setup') ?? [])

  function openSetupWizard(source: Source) {
    navigate(`/setup/${source.id}`)
  }

  $effect(() => {
    let cancelled = false
    // Cleared at the start of each attempt rather than left to linger from a
    // previous one -- and here that is not merely defensive the way it is in
    // Library.svelte, because this effect genuinely does re-run (`id` is a
    // reactive read). The template puts `{#if error}` ahead of the detail
    // branch, so a 404 belonging to a session the user has already navigated
    // away from hides a session that loaded perfectly well, with only a
    // reload to recover.
    error = null
    // `detail` goes with it, back to the Loading… branch. The header renders
    // `id` straight from the prop, so leaving the previous session's detail
    // in place for the length of the fetch shows the new session's name over
    // the old session's rallies -- live, keyboard-driven, and indistinguishable
    // from having loaded. A brief Loading… is the honest state.
    detail = null

    // Reruns only when `id` itself changes (its only reactive read) --
    // i.e. the initial load, or navigating to a different session
    // entirely. Always bumps `rallyRevision`: a different session's rally
    // set must never be read by a QueueController built from the previous
    // one.
    api
      .getSession(id)
      .then((d) => {
        if (cancelled) return
        detail = d
        rallyRevision += 1
      })
      .catch((e) => {
        if (!cancelled) error = e
      })

    // The teardown is what makes a superseded response inert. Without it a
    // slow fetch for the session just left assigns its detail over the one
    // now on screen: the header reads the new session while the rally set,
    // the counts and every keystroke target belong to the old one. Rally ids
    // are globally unique, so a star fired in that state lands on a real
    // rally -- just not the one named above it, and with nothing visible to
    // say so.
    return () => {
      cancelled = true
    }
  })

  function openTimeline(rallyId: string, liveRallies: Rally[]) {
    focusedRallyId = rallyId
    timelineRallies = liveRallies
    mode = 'timeline'
  }

  // TimelineMode edits rally bounds directly against the server from its own
  // local copy of `rallies` -- it never writes back into this component's
  // `detail`. Without a refresh here, returning to the queue would show
  // whatever stale bounds `detail` had from the initial load, contradicting
  // an edit the user just made and persisted. A failed refresh keeps the
  // (stale but harmless) existing `detail` rather than replacing the whole
  // page with an error -- the same "don't halt the flow over a rare
  // failure" reasoning QueueMode's persist-failure handling already uses.
  //
  // Finding 6: refetches *before* flipping `mode`, both inside the same
  // `.then()` -- so `detail` and `mode` change together in one reactive
  // flush. The previous order (`mode = 'queue'` synchronously, refetch
  // after) mounted a fresh QueueMode immediately against the *old* `detail`
  // (nothing had changed yet, so `{#key rallyRevision}` didn't fire), then
  // mounted a second one once the refetch resolved and bumped the key --
  // two QueueController constructions and two full video seeks on every
  // boundary fix, and the first one's undo stack thrown away for nothing.
  function closeTimeline() {
    api
      .getSession(id)
      .then((d) => {
        detail = d
        rallyRevision += 1
        mode = 'queue'
      })
      .catch((e) => {
        console.error('failed to refresh session after leaving timeline mode', e)
        mode = 'queue'
      })
  }

  // Reuses focusedRallyId rather than a field of its own -- it means "the
  // rally the user stepped away from" regardless of which mode did the
  // stepping. Safe to share with TimelineMode's use of the same field: the
  // `{#if mode === 'queue'} ... {:else if mode === 'label'} ... {:else if
  // focusedRallyId}` chain below tests `mode === 'label'` before it ever
  // reaches the TimelineMode branch, so setting focusedRallyId here cannot
  // mis-route into the timeline -- including when rallyId is null (M3: the
  // queue passes null once the pass is finished), since that branch's guard
  // is `focusedRallyId` truthiness only reached in the TimelineMode `{:else
  // if}`, never in the `mode === 'label'` check above it.
  function openLabel(rallyId: string | null) {
    focusedRallyId = rallyId
    mode = 'label'
  }

  // Unlike closeTimeline, this does NOT refetch -- label mode writes only to
  // rally_labels, never a rally's bounds, flags or review status, so
  // `detail` cannot have gone stale. That's the only thing skipping the
  // refetch buys, though: `mode === 'label'` already tears QueueMode down
  // the instant it's set (Svelte destroys the outgoing branch of an
  // `{#if}/{:else if}` chain regardless of `{#key rallyRevision}`), so its
  // undo stack and cursor are gone before this function ever runs. Queue
  // position survives the round trip because openLabel set focusedRallyId
  // first -- the fresh QueueController built on return calls
  // jumpTo(startAtRallyId) against it, the same mechanism openTimeline/
  // closeTimeline already rely on -- not because avoiding a refetch avoided
  // a remount.
  function closeLabel() {
    mode = 'queue'
  }
</script>

<header class="mb-4 flex items-baseline justify-between">
  <div class="flex items-baseline gap-4">
    <button class="text-body text-dim hover:text-fg motion-safe:transition-colors" onclick={() => navigate('/')}>
      ← library
    </button>
    <h1 class="text-display font-semibold">{detail?.session.title ?? id}</h1>
  </div>
  <!--
    Finding 9: QueueMode's persist-failure handling deliberately doesn't
    halt the review queue on a failed star/reject/skip, reasoning that a
    dead server is "already surfaced by the jobs badge" -- but that badge
    previously only rendered in Library's header, never here, which is
    exactly where that reasoning is invoked.
  -->
  <JobsBadge />
</header>

{#if error}
  <ErrorNote {error} subject="session" />
{:else if !detail}
  <p class="text-body text-dim">Loading…</p>
{:else}
  {#if needsSetupSources.length > 0}
    <div class="mb-4 space-y-2 rounded-lg border border-accent/50 bg-accent/5 p-4">
      <h2 class="text-body font-semibold text-accent">Set up sources</h2>
      <p class="text-caption text-accent/80">
        These sources need setup before detection can begin. Pick the rotation and play region for
        each.
      </p>
      <ul class="mt-2 space-y-1">
        {#each needsSetupSources as source (source.id)}
          <li>
            <button
              class="inline-block rounded bg-accent px-3 py-1 text-body font-medium text-bg hover:brightness-110 motion-safe:transition-colors"
              onclick={() => openSetupWizard(source)}
            >
              Set up source {source.idx}
            </button>
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <!--
    Keyed on a revision counter, not the rallies array's identity -- see
    `rallyRevision` above for which callers bump it and why. QueueController
    (in QueueMode) is built once from `detail.rallies` at construction -- by
    design, since it owns queue position/star/reject state for the life of
    the mount -- so the only way for it to see a replaced/edited rally set
    is to remount, which bumping this key forces. Switching between queue/
    timeline via T/esc does NOT bump this, so that toggle never remounts
    either mode needlessly.
  -->
  {#key rallyRevision}
    {#if mode === 'queue'}
      <QueueMode
        {detail}
        onopen_timeline={openTimeline}
        onopen_label={openLabel}
        startAtRallyId={focusedRallyId}
      />
    {:else if mode === 'label'}
      <LabelMode {detail} onclose={closeLabel} startAtRallyId={focusedRallyId} />
    {:else if focusedRallyId}
      <TimelineMode
        {detail}
        rallyId={focusedRallyId}
        initialRallies={timelineRallies ?? undefined}
        onclose={closeTimeline}
      />
    {/if}
  {/key}

  <!--
    Deliberately outside the `{#key rallyRevision}` block above: this panel
    holds its own long-lived state (selected source, slider position, the
    last resegment result) that a re-segment must NOT reset by remounting
    the panel that just triggered it. Its props still update reactively
    when `detail` is replaced -- QueueMode/TimelineMode are the ones that
    need a forced remount (see the comment on `{#key}` above), not this.

    Only render QuadEditor for sources that have a proxy (not needs_setup).
    A needs_setup source has no proxy on disk, so frame.jpg would 404.
  -->
  {#if readySources.length > 0}
    <QuadEditor
      sessionId={id}
      sources={readySources}
      onassigned={() => api.getSession(id).then((d) => (detail = d))}
    />
  {/if}

  <!--
    Only render ResegmentPanel for sources that have features cached (ready
    sources). Passing needs_setup sources would attempt to call api.scores()
    on a source with no features file, a guaranteed failure.
  -->
  {#if readySources.length > 0}
    <ResegmentPanel
      sources={readySources}
      rallies={detail.rallies}
      onresegmented={() =>
        api.getSession(id).then((d) => {
          detail = d
          rallyRevision += 1
        })}
    />
  {/if}
{/if}
