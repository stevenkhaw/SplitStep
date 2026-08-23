<script lang="ts">
  import JobsBadge from '../components/JobsBadge.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Thumb from '../components/Thumb.svelte'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import { reelStatus } from '../lib/status'
  import type { Reel } from '../lib/types'

  let reels = $state<Reel[]>([])
  let error = $state<string | null>(null)
  let loading = $state(true)
  let name = $state('')
  let creating = $state(false)

  $effect(() => {
    let cancelled = false
    loading = true
    error = null
    api
      .listReels()
      .then((r) => {
        if (!cancelled) reels = r
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

  async function create(): Promise<void> {
    // Guarded rather than merely disabled: the form submits on Enter too,
    // and a double Enter would otherwise create two reels with -2 slugs.
    if (creating || !name.trim()) return
    creating = true
    try {
      const reel = await api.createReel(name.trim())
      navigate(`/reels/${reel.slug}`)
    } catch (e) {
      error = String(e)
    } finally {
      creating = false
    }
  }
</script>

<header class="mb-6 flex items-baseline justify-between">
  <h1 class="text-display font-semibold">Reels</h1>
  <div class="flex items-center gap-4">
    <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
            onclick={() => navigate('/')}>Sessions</button>
    <JobsBadge />
  </div>
</header>

<form class="mb-6 flex gap-2" onsubmit={(e) => { e.preventDefault(); create() }}>
  <input
    class="flex-1 rounded border border-line bg-surface px-3 py-1.5 text-body"
    placeholder="New reel name"
    bind:value={name}
    aria-label="New reel name"
  />
  <button
    class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
           hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
    disabled={creating || !name.trim()}
  >
    New reel
  </button>
</form>

{#if error}
  <p class="rounded bg-danger/10 p-3 text-body text-danger">{error}</p>
{:else if loading}
  <p class="text-body text-dim">Loading…</p>
{:else if reels.length === 0}
  <p class="text-body text-dim">
    No reels yet. Finish reviewing a session and compile its points, or name one above.
  </p>
{:else}
  <ul class="space-y-2">
    {#each reels as r (r.id)}
      <li>
        <button
          class="flex w-full items-center gap-4 rounded-lg border border-line bg-surface p-3
                 text-left hover:bg-surface-2 motion-safe:transition-colors"
          onclick={() => navigate(`/reels/${r.slug}`)}
        >
          <!-- The cover is the reel's first clip, which is the frame that
               actually opens the file -- not a generic reel icon. -->
          <Thumb
            session_id={r.thumb?.session_id ?? null}
            idx={r.thumb?.idx ?? null}
            atMs={r.thumb?.at_ms ?? 0}
            alt="first clip of {r.name}"
          />
          <span class="min-w-0 flex-1">
            <span class="block truncate text-title font-semibold">{r.name}</span>
            <span class="mt-1 block font-data text-data text-dim">
              {r.item_count} clip{r.item_count === 1 ? '' : 's'}
            </span>
          </span>
          <StatusBadge badge={reelStatus(r)} />
        </button>
      </li>
    {/each}
  </ul>
{/if}
