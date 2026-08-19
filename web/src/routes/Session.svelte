<script lang="ts">
  import QueueMode from '../components/QueueMode.svelte'
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
{:else if mode === 'queue'}
  <QueueMode {detail} onopen_timeline={openTimeline} />
{:else}
  <p class="text-sm text-neutral-400">Timeline mode — rally {focusedRallyId}</p>
{/if}
