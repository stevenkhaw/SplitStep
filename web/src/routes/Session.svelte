<script lang="ts">
  import JobsBadge from '../components/JobsBadge.svelte'
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
  let error = $state<string | null>(null)
  let mode = $state<'queue' | 'timeline'>('queue')
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
    // Reruns only when `id` itself changes (its only reactive read) --
    // i.e. the initial load, or navigating to a different session
    // entirely. Always bumps `rallyRevision`: a different session's rally
    // set must never be read by a QueueController built from the previous
    // one.
    api
      .getSession(id)
      .then((d) => {
        detail = d
        rallyRevision += 1
      })
      .catch((e) => (error = String(e)))
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
</script>

<header class="mb-4 flex items-baseline justify-between">
  <div class="flex items-baseline gap-4">
    <button class="text-sm text-neutral-400 hover:text-neutral-100" onclick={() => navigate('/')}>
      ← library
    </button>
    <h1 class="text-lg font-semibold">{detail?.session.title ?? id}</h1>
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
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if !detail}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else}
  {#if needsSetupSources.length > 0}
    <div class="mb-4 space-y-2 rounded-lg border border-blue-700/50 bg-blue-500/5 p-4">
      <h2 class="text-sm font-semibold text-blue-300">Set up sources</h2>
      <p class="text-xs text-blue-200/80">
        These sources need setup before detection can begin. Pick the rotation and play region for
        each.
      </p>
      <ul class="mt-2 space-y-1">
        {#each needsSetupSources as source (source.id)}
          <li>
            <button
              class="inline-block rounded bg-blue-600 px-3 py-1 text-sm hover:bg-blue-500"
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
      <QueueMode {detail} onopen_timeline={openTimeline} />
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
