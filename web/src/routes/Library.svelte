<script lang="ts">
  import JobsBadge from '../components/JobsBadge.svelte'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import type { Session } from '../lib/types'

  let sessions = $state<Session[]>([])
  let error = $state<string | null>(null)
  // Session.svelte already has a "Loading…" state for its in-flight fetch;
  // this didn't, so the empty-library copy ("Nothing yet...") was what a
  // user saw for the entire fetch, indistinguishable from a genuinely empty
  // library.
  let loading = $state(true)

  $effect(() => {
    let cancelled = false
    loading = true
    // Cleared at the start of each attempt rather than left to linger from
    // a previous one -- defensive even though nothing here currently
    // retriggers this effect (no reactive reads besides the static `api`
    // import), so a future retry/refresh affordance doesn't inherit a
    // stale error alongside a successful refetch.
    error = null

    api
      .listSessions()
      .then((s) => {
        if (!cancelled) sessions = s
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
      .finally(() => {
        if (!cancelled) loading = false
      })

    return () => {
      cancelled = true
    }
  })

  async function handleSessionClick(session: Session) {
    if (session.status === 'needs_setup') {
      try {
        const detail = await api.getSession(session.id)
        const firstSourceId = detail.sources[0]?.id
        if (firstSourceId) {
          navigate(`/setup/${firstSourceId}`)
        } else {
          // Fallback to session page if no sources
          navigate(`/s/${session.id}`)
        }
      } catch (e) {
        console.error('Failed to navigate to setup:', e)
        navigate(`/s/${session.id}`)
      }
    } else {
      navigate(`/s/${session.id}`)
    }
  }
</script>

<header class="mb-6 flex items-baseline justify-between">
  <h1 class="text-xl font-semibold">Sessions</h1>
  <JobsBadge />
</header>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if loading}
  <p class="text-sm text-neutral-400">Loading…</p>
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
          onclick={() => handleSessionClick(s)}
          aria-label={s.status === 'needs_setup' ? 'set up' : undefined}
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
