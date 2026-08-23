<script lang="ts">
  import ErrorNote from '../components/ErrorNote.svelte'
  import JobsBadge from '../components/JobsBadge.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Thumb from '../components/Thumb.svelte'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import { sessionStatus } from '../lib/status'
  import type { Session } from '../lib/types'

  let sessions = $state<Session[]>([])
  let error = $state<unknown>(null)
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
        if (!cancelled) error = e
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
  <h1 class="text-display font-semibold">Sessions</h1>
  <div class="flex items-center gap-4">
    <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
            onclick={() => navigate('/reels')}>Reels</button>
    <JobsBadge />
  </div>
</header>

{#if error}
  <ErrorNote {error} subject="session" />
{:else if loading}
  <p class="text-body text-dim">Loading…</p>
{:else if sessions.length === 0}
  <p class="text-body text-dim">
    Nothing yet. Drop a video into <code>_inbox/</code> and it will appear here.
  </p>
{:else}
  <ul class="space-y-2">
    {#each sessions as s (s.id)}
      <li>
        <!-- A bordered card rather than a divided list row. The rows carried
             no hover state and no border, so nothing said they were
             clickable at all -- the whole page read as static text. -->
        <button
          class="flex w-full items-center gap-4 rounded-lg border border-line bg-surface p-3
                 text-left hover:border-line hover:bg-surface-2 motion-safe:transition-colors"
          onclick={() => handleSessionClick(s)}
          aria-label={s.status === 'needs_setup' ? 'set up' : undefined}
        >
          <Thumb
            session_id={s.id}
            idx={s.thumb_idx}
            alt="first frame of {s.title}"
          />
          <span class="min-w-0 flex-1">
            <span class="block truncate text-title font-semibold">{s.title}</span>
            <span class="mt-1 flex flex-wrap items-center gap-x-3 font-data text-data text-dim">
              <span>{s.rally_count} rallies</span>
              <span class="text-point">● {s.point_count}</span>
              <span class="text-star">★ {s.starred_count}</span>
            </span>
          </span>
          <StatusBadge badge={sessionStatus(s)} />
        </button>
      </li>
    {/each}
  </ul>
{/if}
