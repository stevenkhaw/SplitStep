<script lang="ts">
  import ErrorNote from '../components/ErrorNote.svelte'
  import FirstRun from '../components/FirstRun.svelte'
  import Mark from '../components/Mark.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Thumb from '../components/Thumb.svelte'
  import { api } from '../lib/api'
  import { startPolling } from '../lib/polling'
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

  // One owner for session fetching. An immediate tick, then a 3s poll that
  // lives only while the library is empty: the first-run card's waiting
  // step promises "this list updates on its own", and the watcher's ingest
  // is the update it waits for. Deliberately NOT a second effect gated on
  // `sessions.length` -- the fetcher reassigns `sessions`, and an effect
  // that reads what its own callback writes tears itself down and restarts
  // a poller whose first tick fires immediately, collapsing the interval
  // into a hot loop. This effect reads no reactive state synchronously, so
  // it runs exactly once per mount.
  $effect(() => {
    let cancelled = false
    const poller = startPolling(async () => {
      try {
        const next = await api.listSessions()
        if (cancelled) return
        sessions = next
        // Success clears a boot-time failure: without this, a server that
        // came back mid-poll stayed hidden behind a stale error note.
        error = null
        if (next.length > 0) poller.stop()
      } catch (e) {
        if (cancelled) return
        // Surface only the first failure as the page state; later ticks
        // keep quietly retrying, which is the recovery this poll exists for.
        if (loading) error = e
      } finally {
        if (!cancelled) loading = false
      }
    }, 3000)
    return () => {
      cancelled = true
      poller.stop()
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

<h1 class="mb-6 text-display font-semibold">Sessions</h1>

{#if error}
  <ErrorNote {error} subject="session" />
{:else if loading}
  <div class="flex justify-center py-12">
    <Mark size={40} state="loading" />
  </div>
{:else if sessions.length === 0}
  <FirstRun />
{:else}
  <!-- One column narrow, two once there is 1800px to give each card real
       width -- a 152px thumbnail alone on a 3400px row was the ultrawide
       complaint in miniature, and a wider single column just stretches the
       same thin row instead of fixing it. -->
  <ul class="grid grid-cols-1 gap-3 min-[1800px]:grid-cols-2">
    {#each sessions as s (s.id)}
      <li>
        <!-- A bordered card rather than a divided list row. The rows carried
             no hover state and no border, so nothing said they were
             clickable at all -- the whole page read as static text. -->
        <button
          class="flex w-full items-center gap-4 rounded-xl border border-line bg-surface p-2.5
                 text-left shadow-[0_1px_0_rgba(255,255,255,0.05)_inset,0_12px_28px_-18px_#000]
                 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2
                 focus-visible:outline-fg motion-safe:transition-colors
                 motion-safe:duration-quick"
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
