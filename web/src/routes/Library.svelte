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
  let clickBusy = $state(false)

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
    if (clickBusy) return
    clickBusy = true
    try {
      if (session.status === 'needs_setup') {
        try {
          const detail = await api.getSession(session.id)
          // handle_ingest flips the SESSION to needs_setup unconditionally,
          // and find_or_create_session_for_date reuses a session at any
          // status -- so a second clip dropped on a day whose first clip is
          // already reviewed flips the session to needs_setup while that
          // first source stays 'ready'. Opening the wizard on sources[0]
          // would land on the reviewed source; confirming there rebuilds it
          // and discards its hand-edited rally boundaries. Open on whichever
          // source actually needs setup.
          //
          // No source may match at all, and that is a real, reachable case
          // -- not just defensive empty-array handling: handle_build_proxy
          // sets the newly-ingested SOURCE to 'building' *before* the
          // SESSION flips to 'detecting' once the transcode finishes (7+
          // minutes on 4K). For that whole window the session still reads
          // 'needs_setup' (so this affordance is still showing) while its
          // only unset-up source has already moved past that status. Landing
          // on the session page instead of guessing a source is what keeps
          // "did my clip finish yet?" from reopening the wizard on an
          // already-reviewed source.
          const target = detail.sources.find((s) => s.status === 'needs_setup')
          if (target) {
            navigate(`/setup/${target.id}`)
          } else {
            navigate(`/s/${session.id}`)
          }
        } catch (e) {
          console.error('Failed to navigate to setup:', e)
          navigate(`/s/${session.id}`)
        }
      } else {
        navigate(`/s/${session.id}`)
      }
    } finally {
      clickBusy = false
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
            {s.rally_count} rallies · P{s.point_count} · ★{s.starred_count} · {s.status}
          </span>
        </button>
      </li>
    {/each}
  </ul>
{/if}
