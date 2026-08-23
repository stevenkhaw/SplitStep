<script lang="ts">
  import { api } from '../lib/api'
  import { activeJobsLabel, jobRows } from '../lib/jobs'
  import { startPolling } from '../lib/polling'
  import type { Job } from '../lib/types'

  let jobs = $state<Job[]>([])
  let open = $state(false)
  // Elapsed has to advance between polls or the badge looks frozen through a
  // fifteen-minute detect. The data poll stays at 3s (it hits sqlite); this
  // is a local clock only, so it can tick every second for nothing.
  let nowMs = $state(Date.now())

  $effect(() => {
    const poller = startPolling(async () => {
      try {
        jobs = await api.jobs()
        nowMs = Date.now()
      } catch {
        // the server may be restarting; keep polling
      }
    }, 3000)
    const clock = setInterval(() => {
      nowMs = Date.now()
    }, 1000)

    return () => {
      poller.stop()
      clearInterval(clock)
    }
  })

  const label = $derived(activeJobsLabel(jobs, nowMs))
  const rows = $derived(jobRows(jobs, nowMs))
  const failed = $derived(jobs.filter((j) => j.status === 'failed'))

  // Closes itself once the queue drains and nothing failed: the badge is the
  // only control that dismisses the panel, and it stops rendering at the
  // same moment, which would otherwise strand an empty box on screen.
  $effect(() => {
    if (rows.length === 0) open = false
  })
</script>

<!--
  Escape is also TimelineMode's and LabelMode's "leave this mode" key, and
  both listen on `window` too. With the panel open, one Escape therefore
  closes the panel *and* leaves the mode. Deliberately not fought: suppressing
  the other handler would mean stopImmediatePropagation plus a guarantee about
  which component registered its listener first, which is a real ordering
  dependence to carry for a rare overlap between two non-destructive actions.
  Closing both is mildly surprising; neither loses work.
-->
<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape' && open) open = false
  }}
/>

{#if label || failed.length > 0}
  <!-- `relative` sits on the wrapper rather than the button because the panel
       is wider than the badge and hangs off its right edge. -->
  <div class="relative">
    <button
      type="button"
      class="flex items-center gap-2 rounded-full border px-3 py-1 font-data text-caption
             {failed.length > 0 && !label
        ? 'border-danger/30 bg-danger/10 text-danger'
        : 'border-accent/30 bg-accent/10 text-accent'}"
      aria-expanded={open}
      aria-label={label ? `${label}. Show the job queue` : 'Show the job queue'}
      onclick={() => (open = !open)}
    >
      {#if label}
        <!-- A slow pulse, not a spinner: the worker is single-threaded and
             these run for minutes, so a spinner would imply a frame-by-frame
             responsiveness nothing here is measuring. -->
        <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-current motion-safe:animate-pulse"></span>
        {label}
      {:else}
        {failed.length} failed
      {/if}
    </button>

    {#if open && rows.length > 0}
      <div
        class="absolute right-0 z-40 mt-2 w-72 overflow-hidden rounded-lg border border-line
               bg-surface shadow-lg"
      >
        <p
          class="border-b border-line px-3 py-2 font-data text-caption tracking-wide text-faint uppercase"
        >
          Queue
        </p>
        <ul>
          {#each rows as r (r.id)}
            <li
              class="flex items-center gap-2 border-b border-line px-3 py-2 font-data text-caption"
            >
              <span
                class="h-1.5 w-1.5 shrink-0 rounded-full {r.status === 'running'
                  ? 'bg-accent'
                  : r.status === 'failed'
                    ? 'bg-danger'
                    : 'bg-faint'}"
              ></span>
              <span
                class={r.status === 'failed'
                  ? 'text-danger'
                  : r.status === 'running'
                    ? 'text-fg'
                    : 'text-dim'}
              >
                {r.phase}
              </span>
              <span class="ml-auto shrink-0 text-faint">
                {#if r.status === 'running'}
                  {r.elapsed}{#if r.progress > 0}
                    &middot; {Math.min(99, Math.floor(r.progress * 100))}%
                  {/if}
                {:else if r.status === 'failed'}
                  failed
                {:else}
                  queued
                {/if}
              </span>
            </li>
            {#if r.error}
              <li class="border-b border-line px-3 pb-2 font-data text-caption text-danger">
                {r.error}
              </li>
            {/if}
          {/each}
        </ul>
      </div>
    {/if}
  </div>
{/if}
