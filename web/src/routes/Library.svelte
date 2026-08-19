<script lang="ts">
  import JobsBadge from '../components/JobsBadge.svelte'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import type { Session } from '../lib/types'

  let sessions = $state<Session[]>([])
  let error = $state<string | null>(null)

  $effect(() => {
    let cancelled = false

    api
      .listSessions()
      .then((s) => {
        if (!cancelled) sessions = s
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })

    return () => {
      cancelled = true
    }
  })
</script>

<header class="mb-6 flex items-baseline justify-between">
  <h1 class="text-xl font-semibold">Sessions</h1>
  <JobsBadge />
</header>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if sessions.length === 0}
  <p class="text-sm text-neutral-400">
    Nothing yet. Drop a video into <code>_inbox/</code> and it will appear here.
  </p>
{:else}
  <ul class="divide-y divide-neutral-800">
    {#each sessions as s (s.id)}
      <li>
        <button
          class="flex w-full items-baseline justify-between py-3 text-left hover:bg-neutral-900"
          onclick={() => navigate(`/s/${s.id}`)}
        >
          <span class="font-medium">{s.title}</span>
          <span class="font-mono text-xs text-neutral-400">
            {s.rally_count} rallies · ★{s.starred_count} · {s.status}
          </span>
        </button>
      </li>
    {/each}
  </ul>
{/if}
