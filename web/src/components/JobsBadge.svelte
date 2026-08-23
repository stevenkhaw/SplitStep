<script lang="ts">
  import { api } from '../lib/api'
  import { activeJobsLabel } from '../lib/jobs'
  import { startPolling } from '../lib/polling'
  import type { Job } from '../lib/types'

  let jobs = $state<Job[]>([])

  $effect(() => {
    const poller = startPolling(async () => {
      try {
        jobs = await api.jobs()
      } catch {
        // the server may be restarting; keep polling
      }
    }, 3000)

    return () => poller.stop()
  })

  const label = $derived(activeJobsLabel(jobs))
  const failed = $derived(jobs.filter((j) => j.status === 'failed'))
</script>

{#if label}
  <span class="rounded bg-accent/20 px-2 py-1 text-caption text-accent">{label}</span>
{/if}
{#if failed.length > 0}
  <span
    class="rounded bg-danger/20 px-2 py-1 text-caption text-danger"
    title={failed[0].error ?? ''}
  >
    {failed.length} failed
  </span>
{/if}
