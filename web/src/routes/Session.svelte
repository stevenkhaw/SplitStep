<script lang="ts">
  import QueueMode from '../components/QueueMode.svelte'
  import TimelineMode from '../components/TimelineMode.svelte'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import type { SessionDetail } from '../lib/types'

  interface Props {
    id: string
  }
  let { id }: Props = $props()

  let detail = $state<SessionDetail | null>(null)
  let error = $state<string | null>(null)
  let mode = $state<'queue' | 'timeline'>('queue')
  let focusedRallyId = $state<string | null>(null)

  $effect(() => {
    api
      .getSession(id)
      .then((d) => (detail = d))
      .catch((e) => (error = String(e)))
  })

  function openTimeline(rallyId: string) {
    focusedRallyId = rallyId
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
  function closeTimeline() {
    mode = 'queue'
    api
      .getSession(id)
      .then((d) => {
        detail = d
      })
      .catch((e) => {
        console.error('failed to refresh session after leaving timeline mode', e)
      })
  }
</script>

<header class="mb-4 flex items-baseline justify-between">
  <button class="text-sm text-neutral-400 hover:text-neutral-100" onclick={() => navigate('/')}>
    ← library
  </button>
  <h1 class="text-lg font-semibold">{detail?.session.title ?? id}</h1>
</header>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if !detail}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else}
  <!--
    Keyed on the rallies array's identity, not just `detail`: a re-segment
    (Task 13) replaces `detail` on this already-mounted Session, and the
    rally set it carries is what QueueMode/TimelineMode must never hold a
    stale snapshot of. QueueController (in QueueMode) is built once from
    `detail.rallies` at construction -- by design, since it owns queue
    position/star/reject state for the life of the mount -- so the only way
    for it to see a replaced rally set is to remount, which this forces.
    Switching between queue/timeline via T/esc does NOT change this key, so
    that toggle never remounts either mode needlessly.
  -->
  {#key detail.rallies}
    {#if mode === 'queue'}
      <QueueMode {detail} onopen_timeline={openTimeline} />
    {:else if focusedRallyId}
      <TimelineMode {detail} rallyId={focusedRallyId} onclose={closeTimeline} />
    {/if}
  {/key}
{/if}
