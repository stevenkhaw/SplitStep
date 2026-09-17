<script lang="ts">
  import ClipsPanel from '../components/ClipsPanel.svelte'
  import ErrorNote from '../components/ErrorNote.svelte'
  import LabelMode from '../components/LabelMode.svelte'
  import Mark from '../components/Mark.svelte'
  import QueueMode from '../components/QueueMode.svelte'
  import QuadEditor from '../components/QuadEditor.svelte'
  import ResegmentPanel from '../components/ResegmentPanel.svelte'
  import TimelineMode from '../components/TimelineMode.svelte'
  import { api } from '../lib/api'
  import { appmode } from '../lib/appmode.svelte'
  import { navigate } from '../lib/router.svelte'
  import { resolveSelectedTab, scopeToSource, sourceTabs } from '../lib/sources'
  import type { ExportResult, Rally, SessionDetail, Source } from '../lib/types'

  interface Props {
    id: string
  }
  let { id }: Props = $props()

  let detail = $state<SessionDetail | null>(null)
  let error = $state<unknown>(null)
  let mode = $state<'queue' | 'timeline' | 'label'>('queue')
  let focusedRallyId = $state<string | null>(null)
  // Which video's tab is selected, or null for "no tab UI" -- a session
  // with one (or zero) reviewable sources, where `tabs.length <= 1` below
  // and this never leaves null, so the page stays pixel-identical to
  // before tabs existed. Distinct from "tabs exist but none picked yet":
  // the fetch sites below always resolve this to a real tab id the same
  // tick `detail` lands, via `resolveSelectedSource`.
  let selectedSourceId = $state<string | null>(null)
  // Whether the queue's current pass includes rejected rallies -- H flips
  // this. Lives here, not in QueueMode, because QueueController reads it
  // only at construction (see its one-time `queue` snapshot): changing the
  // filter needs a fresh controller, which is exactly what bumping
  // rallyRevision below forces.
  let showRejected = $state(false)
  // The live-merged rallies QueueMode hands to openTimeline (see
  // QueueController.liveSnapshot) -- threaded through so TimelineMode's
  // OverviewBand can color a rally starred/rejected earlier in this queue
  // session correctly. Falls back to `detail.rallies` inside TimelineMode
  // itself when null (its own default), so this only needs to be set, never
  // explicitly cleared.
  let timelineRallies = $state<Rally[] | null>(null)

  // The most recent successful export kickoff, threaded down to ClipsPanel
  // so it can open itself and poll for the clips landing (see QueueMode's
  // `onexport` prop and ClipsPanel's `exportResult` prop). A fresh object
  // every call -- ClipsPanel reacts to it by identity, not value, since two
  // exports can legitimately report an identical `{queued:0,...}` shape and
  // each is still its own event. Deliberately NOT reset on a session/tab
  // switch: it names something that already happened, not something scoped
  // to the currently selected source, and ClipsPanel's own sessionId-swap
  // effect already clears the poll target that would otherwise read it.
  let lastExport = $state<ExportResult | null>(null)

  // Bumped only when the rally *set* actually needs QueueMode/TimelineMode
  // to remount: the initial load (or a navigation to a different session
  // entirely, below), re-segmentation (a different id/count), a bounds
  // edit made in TimelineMode (same ids, but QueueController/TimelineMode
  // each hold their own frozen copy of start_ms/end_ms that a remount is
  // what refreshes), or switching video tabs -- a different `selectedSourceId`
  // scopes `detail` to a different rally set the same way a re-segment does,
  // and QueueController/TimelineMode are just as unaware of that swap as they
  // are of a server-side one. QuadEditor's onassigned refetch deliberately
  // does NOT bump this: assigning a play-region preset never changes rally
  // content, so forcing a remount there would only needlessly discard
  // QueueMode's undo stack for a change it has nothing to do with (Finding 6,
  // "QuadEditor's onassigned has the same shape"). See the `{#key}` below
  // for where this is consumed.
  let rallyRevision = $state(0)

  const needsSetupSources = $derived(detail?.sources.filter((s) => s.status === 'needs_setup') ?? [])
  const readySources = $derived(detail?.sources.filter((s) => s.status !== 'needs_setup') ?? [])

  // One entry per reviewable (non-needs_setup) source, ordered by idx. Empty
  // for a session with zero or one such source, which is exactly when the
  // tab strip below renders nothing and `selectedSourceId` stays null --
  // a single-video session must be pixel-identical to before tabs existed.
  const tabs = $derived(detail ? sourceTabs(detail.sources, detail.rallies) : [])

  // Called from both places `detail` is replaced by a fetch (the load
  // effect and closeTimeline's refetch) so the two cannot drift on the rule
  // itself -- that rule (resolveSelectedTab, lib/sources.ts) is pure and
  // tested there; this just wires its result back into state.
  function resolveSelectedSource(d: SessionDetail): void {
    selectedSourceId = resolveSelectedTab(sourceTabs(d.sources, d.rallies), selectedSourceId)
  }

  // Switching tabs scopes the mode block to a different rally set, which is
  // the same "QueueController/TimelineMode are holding stale state" problem
  // a re-segment causes -- so it resets the same fields closeTimeline/openLabel
  // reset on a mode change, plus bumps rallyRevision (see its comment above)
  // to force the remount.
  function selectTab(sourceId: string): void {
    selectedSourceId = sourceId
    mode = 'queue'
    focusedRallyId = null
    timelineRallies = null
    rallyRevision += 1
  }

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
        resolveSelectedSource(d)
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

  // H in QueueMode. A fresh QueueController is the only way the includeRejected
  // filter changes (see the prop's doc comment on QueueMode), and reuses
  // focusedRallyId/startAtRallyId, the same "come back on this rally" trick
  // closeTimeline uses, so the reviewer does not lose their place.
  //
  // Refetches first, mirroring closeTimeline's shape: QueueMode never writes
  // a verdict back into `detail` (see its apply(), and the comment on
  // `sessionRallies` above), so remounting straight from the existing
  // snapshot would judge "is this rally rejected" by whatever `detail`
  // happened to hold at the last full load -- forgetting every reject and
  // un-reject made during this pass. Concretely: reject a rally, un-reject
  // it, then press H to hide rejected rallies again -- without this
  // refetch, the stale snapshot still says rejected and the rally the
  // server had already restored silently vanishes again. `showRejected`
  // only flips inside the `.then`, alongside `detail`, so a failed refetch
  // leaves both exactly as they were rather than remounting the queue
  // against data that cannot back up the toggle it is about to show.
  //
  // Plain `let`, not `$state`: nothing renders it, it only gates re-entrancy.
  // A held H (OS key-repeat) or a fast double-press would otherwise start a
  // second `getSession` before the first resolves; each `.then` reads
  // whatever `showRejected` is live at the time IT resolves and flips it
  // again, so two in-flight requests race and whichever response lands last
  // -- not whichever was fired last -- decides the final filter, with
  // `detail` potentially regressing to the older response's snapshot too.
  // Dropping every call while one is already in flight makes a held key a
  // single toggle instead of a queue of them.
  let togglingRejected = false

  function toggleRejected(rallyId: string | null) {
    if (togglingRejected) return
    togglingRejected = true
    api
      .getSession(id)
      .then((d) => {
        detail = d
        resolveSelectedSource(d)
        showRejected = !showRejected
        focusedRallyId = rallyId
        rallyRevision += 1
      })
      .catch((e) => {
        console.error('failed to refresh session before toggling show-rejected', e)
      })
      .finally(() => {
        togglingRejected = false
      })
  }

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
        resolveSelectedSource(d)
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
    // Friend mode hides label mode rather than deleting it; the keybinding
    // an old muscle-memory presses must not open a surface the reference no
    // longer lists -- and every corpus write should stay behind a surface
    // the reviewer can see.
    if (appmode.current === 'friend') return
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

<div class="mb-5">
  <button class="font-data text-caption text-faint hover:text-dim" onclick={() => navigate('/')}>
    Sessions
  </button>
  <span class="font-data text-caption text-faint"> › </span>
  <h1 class="mt-1 text-display font-semibold">{id}</h1>
</div>

{#if error}
  <ErrorNote {error} subject="session" />
{:else if !detail}
  <div class="flex justify-center py-12">
    <Mark size={40} state="loading" />
  </div>
{:else}
  {#if needsSetupSources.length > 0}
    <div class="mb-4 space-y-2 rounded-lg border border-line bg-surface p-4">
      <h2 class="text-body font-semibold text-fg">Set up sources</h2>
      <p class="text-caption text-dim">
        These sources need setup before detection can begin. Pick the rotation and play region for
        each.
      </p>
      <ul class="mt-2 space-y-1">
        {#each needsSetupSources as source (source.id)}
          <li>
            <button
              class="inline-block rounded bg-fg px-3 py-1 text-body font-medium text-bg hover:bg-fg/90 motion-safe:transition-colors"
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
    Only rendered once there is more than one video to choose between --
    `tabs` is already empty/single for a needs_setup-only or one-source
    session, and a lone tab would be a control with nothing to switch to.
    Queue/label/timeline all scope to whichever tab is selected below; the
    quad editor and re-segment panel are deliberately never scoped (see
    their own props further down) since they carry their own source pickers.
  -->
  {#if tabs.length > 1}
    <div class="mb-4 flex gap-1 border-b border-line">
      {#each tabs as tab (tab.id)}
        <button
          class="border-b-2 px-3 py-1.5 font-data text-data motion-safe:transition-colors
                 {selectedSourceId === tab.id
            ? 'border-fg text-fg'
            : 'border-transparent text-dim hover:text-fg'}"
          onclick={() => selectTab(tab.id)}
        >
          Video {String(tab.idx).padStart(2, '0')} · {tab.rallyCount}
        </button>
      {/each}
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
    either mode needlessly. Switching video tabs (`selectTab`) bumps it too,
    for the same reason: it scopes `detail` to a different source's rallies,
    which QueueController/TimelineMode need a fresh construction to see.
  -->
  {#key rallyRevision}
    {#if mode === 'queue'}
      <QueueMode
        detail={scopeToSource(detail, selectedSourceId)}
        sourceStatus={selectedSourceId
          ? (detail.sources.find((s) => s.id === selectedSourceId)?.status ??
            detail.session.status)
          : detail.session.status}
        onopen_timeline={openTimeline}
        onopen_label={openLabel}
        startAtRallyId={focusedRallyId}
        onexport={(result) => (lastExport = result)}
        sessionRallies={detail.rallies}
        onscoring={(rules) => {
          if (detail) detail.session.scoring = rules
        }}
        {showRejected}
        ontoggle_rejected={toggleRejected}
      />
    {:else if mode === 'label'}
      <LabelMode
        detail={scopeToSource(detail, selectedSourceId)}
        onclose={closeLabel}
        startAtRallyId={focusedRallyId}
      />
    {:else if focusedRallyId}
      <TimelineMode
        detail={scopeToSource(detail, selectedSourceId)}
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

    And only in dev mode: re-segmenting destroys manual edits and hand-made
    rallies, which makes it a tuning tool, not a review tool -- friend mode
    keeps it behind the Advanced toggle (spec 2026-08-26, "friend mode hides
    tuning tools").
  -->
  {#if appmode.current === 'dev' && readySources.length > 0}
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

  <!--
    Not gated on readySources/tabs the way QuadEditor/ResegmentPanel are --
    clips already cut are a fact about the session's clips/ folder, not
    about any one source's detection state, so this stays visible even for
    a needs_setup-only session (which can still hold clips from before a
    re-ingest, however unlikely). Outside `{#key rallyRevision}` for the
    same reason as the two panels above: it owns its own long-lived state
    (which video's watch is expanded, an in-progress export poll) that a
    re-segment or a tab switch must not reset by remounting it.
  -->
  <ClipsPanel sessionId={id} exportResult={lastExport} />
{/if}
