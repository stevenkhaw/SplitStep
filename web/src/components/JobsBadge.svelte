<script lang="ts">
  import { api } from '../lib/api'
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

  const active = $derived(jobs.filter((j) => j.status === 'queued' || j.status === 'running'))
  const failed = $derived(jobs.filter((j) => j.status === 'failed'))
</script>

{#if active.length > 0}
  <span class="rounded bg-blue-500/20 px-2 py-1 text-xs text-blue-300">
    {active.length} job{active.length === 1 ? '' : 's'} running
  </span>
{/if}
{#if failed.length > 0}
  <span
    class="rounded bg-red-500/20 px-2 py-1 text-xs text-red-300"
    title={failed[0].error ?? ''}
  >
    {failed.length} failed
  </span>
{/if}
