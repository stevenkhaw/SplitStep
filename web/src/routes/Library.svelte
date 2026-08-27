<script lang="ts">
  import ErrorNote from '../components/ErrorNote.svelte'
  import FirstRun from '../components/FirstRun.svelte'
  import JobsBadge from '../components/JobsBadge.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Thumb from '../components/Thumb.svelte'
  import { api } from '../lib/api'
  import { appmode } from '../lib/appmode.svelte'
  import { startPolling } from '../lib/polling'
  import { navigate } from '../lib/router.svelte'
  import { formatBytes } from '../lib/size'
  import { sessionStatus } from '../lib/status'
  import type { Session } from '../lib/types'

  let sessions = $state<Session[]>([])
  let settingsOpen = $state(false)
  // null until it loads; the header renders nothing rather than a
  // placeholder that would churn. Fetched once per visit -- the number
  // moves at ingest/export pace, not poll pace.
  let libraryBytes = $state<number | null>(null)
  $effect(() => {
    api
      .libraryStats()
      .then((s) => (libraryBytes = s.bytes))
      .catch(() => {})
  })
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

  // While the library is empty the page polls: the first-run card's waiting
  // step promises "this list updates on its own", and the watcher's ingest
  // is the update it is waiting for. Tears down the moment a session
  // exists, so a populated library never pays for it.
  $effect(() => {
    if (loading || sessions.length > 0) return
    const poller = startPolling(async () => {
      try {
        sessions = await api.listSessions()
      } catch {
        // the server may be restarting; keep polling
      }
    }, 3000)
    return () => poller.stop()
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
    {#if libraryBytes !== null}
      <!-- The keep-everything policy's one disk affordance: informational,
           no action attached (spec 2026-08-26). -->
      <span class="font-data text-data text-faint">{formatBytes(libraryBytes)}</span>
    {/if}
    <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
            onclick={() => navigate('/reels')}>Reels</button>
    <div class="relative">
      <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
              aria-expanded={settingsOpen}
              onclick={() => (settingsOpen = !settingsOpen)}>Settings</button>
      {#if settingsOpen}
        <div class="absolute right-0 z-10 mt-2 w-72 rounded-lg border border-line bg-surface p-4
                    text-left shadow-2xl">
          <label class="flex items-start gap-2 text-body">
            <input
              type="checkbox"
              class="mt-1 accent-accent"
              checked={appmode.current === 'dev'}
              onchange={(e) => appmode.set(e.currentTarget.checked ? 'dev' : 'friend')}
            />
            <span>
              Advanced tools — label mode and re-segment
              <span class="mt-1 block text-caption text-faint">
                Hidden in friend mode so a stray keypress can't write to the
                training corpus or rebuild a reviewed session.
              </span>
            </span>
          </label>
        </div>
      {/if}
    </div>
    <JobsBadge />
  </div>
</header>

{#if error}
  <ErrorNote {error} subject="session" />
{:else if loading}
  <p class="text-body text-dim">Loading…</p>
{:else if sessions.length === 0}
  <FirstRun sessionCount={sessions.length} {loading} />
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
