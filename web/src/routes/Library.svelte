<script lang="ts">
  import ErrorNote from '../components/ErrorNote.svelte'
  import FirstRun from '../components/FirstRun.svelte'
  import JobsBadge from '../components/JobsBadge.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Thumb from '../components/Thumb.svelte'
  import { api } from '../lib/api'
  import { appmode } from '../lib/appmode.svelte'
  import { librarySize } from '../lib/librarysize.svelte'
  import { startPolling } from '../lib/polling'
  import { navigate } from '../lib/router.svelte'
  import { formatBytes } from '../lib/reels'
  import { changeLibrary, inShell } from '../lib/shell'
  import { sessionStatus } from '../lib/status'
  import type { Session } from '../lib/types'

  let sessions = $state<Session[]>([])
  let settingsOpen = $state(false)
  let switchError = $state<string | null>(null)

  async function switchLibrary() {
    switchError = null
    try {
      await changeLibrary()
      // No success branch: the shell navigates the window away to the
      // chooser, so reaching the next line means it did not.
    } catch (e) {
      switchError = e instanceof Error ? e.message : String(e)
    }
  }
  // Stale-while-revalidate: the store keeps the last figure across route
  // remounts, so the header doesn't blank and the server doesn't re-walk
  // the drive on every navigation back to this page.
  void librarySize.refresh()
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

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape' && settingsOpen) settingsOpen = false
  }}
/>

<header class="mb-6 flex items-baseline justify-between">
  <h1 class="text-display font-semibold">Sessions</h1>
  <div class="flex items-center gap-4">
    {#if librarySize.bytes !== null}
      <!-- The keep-everything policy's one disk affordance: informational,
           no action attached (spec 2026-08-26). -->
      <span class="font-data text-data text-faint">{formatBytes(librarySize.bytes)}</span>
    {/if}
    <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
            onclick={() => navigate('/reels')}>Reels</button>
    <div class="relative">
      <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
              aria-expanded={settingsOpen}
              onclick={() => (settingsOpen = !settingsOpen)}>Settings</button>
      {#if settingsOpen}
        <!-- Same dismissal pair the jobs panel beside this one offers:
             Escape (svelte:window below) and clicking anywhere else. The
             backdrop is transparent -- it exists to catch the outside
             click, not to dim the page for a two-line popover. -->
        <div
          class="fixed inset-0 z-10"
          role="presentation"
          onclick={() => (settingsOpen = false)}
        ></div>
        <div class="absolute right-0 z-20 mt-2 w-72 rounded-lg border border-line bg-surface p-4
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
          {#if inShell()}
            <!-- Shell-only: a browser tab has nothing to ask. Separated by a
                 rule because it is a different kind of thing from the toggle
                 above -- that changes what this library shows, this leaves
                 the library entirely. -->
            <div class="mt-3 border-t border-line pt-3">
              <button class="text-body text-accent" onclick={switchLibrary}>
                Change library…
              </button>
              <span class="mt-1 block text-caption text-faint">
                Nothing is moved. This library stays where it is, and you can
                come back to it.
              </span>
              {#if switchError}
                <span class="mt-1 block text-caption text-danger">{switchError}</span>
              {/if}
            </div>
          {/if}
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
  <FirstRun />
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
