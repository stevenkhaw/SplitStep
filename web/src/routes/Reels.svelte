<script lang="ts">
  import JobsBadge from '../components/JobsBadge.svelte'
  import { api } from '../lib/api'
  import { reelStateLabel } from '../lib/reels'
  import { navigate } from '../lib/router.svelte'
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
  <h1 class="text-xl font-semibold">Reels</h1>
  <div class="flex items-center gap-4">
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200"
            onclick={() => navigate('/')}>Sessions</button>
    <JobsBadge />
  </div>
</header>

<form class="mb-6 flex gap-2" onsubmit={(e) => { e.preventDefault(); create() }}>
  <input
    class="flex-1 rounded border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm"
    placeholder="New reel name"
    bind:value={name}
    aria-label="New reel name"
  />
  <button
    class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
           hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
    disabled={creating || !name.trim()}
  >
    New reel
  </button>
</form>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if loading}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else if reels.length === 0}
  <p class="text-sm text-neutral-400">
    No reels yet. Finish reviewing a session and compile its points, or name one above.
  </p>
{:else}
  <ul class="divide-y divide-neutral-800">
    {#each reels as r (r.id)}
      <li>
        <button
          class="flex w-full items-baseline justify-between py-3 text-left hover:bg-neutral-900"
          onclick={() => navigate(`/reels/${r.slug}`)}
        >
          <span class="font-medium">{r.name}</span>
          <span class="font-mono text-xs text-neutral-400">
            {r.item_count} clips · {reelStateLabel(r)}
          </span>
        </button>
      </li>
    {/each}
  </ul>
{/if}
