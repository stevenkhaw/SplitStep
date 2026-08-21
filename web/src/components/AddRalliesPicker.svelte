<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { spanKey, spanRef } from '../lib/reels'
  import { formatDuration, formatTs } from '../lib/time'
  import type { Rally, Session, SpanRef } from '../lib/types'

  type Filter = 'points' | 'starred' | 'all'

  interface Props {
    existing: SpanRef[]
    defaultSessionId?: string | null
    onadd: (spans: SpanRef[]) => void
    onclose: () => void
  }

  let { existing, defaultSessionId = null, onadd, onclose }: Props = $props()

  let sessions = $state<Session[]>([])
  // Deliberately a one-time seed, not a reactive read: the picker is seeded
  // once from the reel's last item, and the parent remounts it when adding to
  // a different reel, so following a later prop change would fight the user's
  // own session selection. `untrack` tells svelte-check this one-time read is
  // intentional rather than an accidental non-reactive reference.
  let sessionId = $state<string | null>(untrack(() => defaultSessionId))
  let rallies = $state<Rally[]>([])
  let filter = $state<Filter>('points')
  let checked = $state(new Set<string>())
  let error = $state<string | null>(null)
  let loading = $state(true)

  const alreadyIn = $derived(new Set(existing.map(spanKey)))

  $effect(() => {
    let cancelled = false
    api
      .listSessions()
      .then((s) => {
        if (cancelled) return
        sessions = s
        // A reel is session-agnostic, so a hand-made one has no session to
        // default to -- fall back to the newest, which is what a reviewer
        // building a reel by hand almost always wants.
        if (!sessionId && s.length > 0) sessionId = s[0].id
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
    return () => {
      cancelled = true
    }
  })

  $effect(() => {
    const id = sessionId
    if (!id) return
    let cancelled = false
    loading = true
    api
      .getSession(id)
      .then((d) => {
        if (cancelled) return
        rallies = d.rallies
        // Cleared on a session change: a checked span from the previous
        // session would otherwise be added invisibly, since it is no longer
        // rendered anywhere in this list.
        checked = new Set()
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

  const shown = $derived(
    rallies
      // Rejected is a ruling that this was never a rally at all -- a bad
      // detection is not a clip anyone wants in a reel, under any filter.
      .filter((r) => !r.rejected)
      .filter((r) => (filter === 'points' ? r.point : filter === 'starred' ? r.starred : true)),
  )

  const selectable = $derived(shown.filter((r) => !alreadyIn.has(spanKey(r))))
  const selected = $derived(selectable.filter((r) => checked.has(r.id)))

  function toggle(rally: Rally): void {
    // Reassigned rather than mutated: Svelte 5 tracks the binding, and an
    // in-place Set.add() would not re-render the list.
    const next = new Set(checked)
    if (next.has(rally.id)) next.delete(rally.id)
    else next.add(rally.id)
    checked = next
  }

  function selectAll(): void {
    checked = new Set(selectable.map((r) => r.id))
  }

  function add(): void {
    if (selected.length === 0) return
    // spanRef narrows to the three fields the server keys on -- posting a
    // whole Rally would send fields it ignores today and might not later.
    onadd(selected.map(spanRef))
  }
</script>

<div class="rounded-lg border border-neutral-800 p-4">
  <div class="mb-3 flex items-baseline justify-between">
    <h2 class="text-sm font-semibold">Add rallies</h2>
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200" onclick={onclose}>
      close
    </button>
  </div>

  {#if error}
    <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
  {:else}
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <select
        class="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
        aria-label="Session"
        bind:value={sessionId}
      >
        {#each sessions as s (s.id)}
          <option value={s.id}>{s.title}</option>
        {/each}
      </select>

      {#each ['points', 'starred', 'all'] as f (f)}
        <button
          data-filter={f}
          class="rounded border px-2 py-1 font-mono text-xs
                 {filter === f
                   ? 'border-blue-500 text-blue-300'
                   : 'border-neutral-700 text-neutral-400 hover:bg-neutral-800'}"
          onclick={() => (filter = f as Filter)}
        >{f}</button>
      {/each}

      <button
        data-select-all
        class="ml-auto font-mono text-xs text-neutral-400 hover:text-neutral-200
               disabled:opacity-40"
        disabled={selectable.length === 0}
        onclick={selectAll}
      >select all</button>
    </div>

    {#if loading}
      <p class="text-sm text-neutral-400">Loading…</p>
    {:else if shown.length === 0}
      <p class="text-sm text-neutral-400">Nothing matches this filter in that session.</p>
    {:else}
      <ul class="max-h-72 divide-y divide-neutral-800 overflow-y-auto">
        {#each shown as r (r.id)}
          {@const inReel = alreadyIn.has(spanKey(r))}
          <li>
            <label class="flex items-center gap-3 py-2 text-sm
                          {inReel ? 'text-neutral-500' : ''}">
              <input
                type="checkbox"
                checked={inReel || checked.has(r.id)}
                disabled={inReel}
                onchange={() => toggle(r)}
              />
              <span class="font-mono text-xs tabular-nums">{formatTs(r.start_ms)}</span>
              <span class="font-mono text-xs tabular-nums text-neutral-400">
                {formatDuration(r.end_ms - r.start_ms)}
              </span>
              {#if r.point}<span class="font-mono text-xs text-neutral-400">P</span>{/if}
              {#if r.starred}<span class="font-mono text-xs text-amber-300">★</span>{/if}
              {#if inReel}
                <span class="ml-auto font-mono text-xs text-neutral-600">in reel</span>
              {/if}
            </label>
          </li>
        {/each}
      </ul>
    {/if}

    <button
      data-add
      class="mt-3 rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs
             text-neutral-200 hover:bg-neutral-800 disabled:cursor-not-allowed
             disabled:opacity-40"
      disabled={selected.length === 0}
      onclick={add}
    >
      Add {selected.length} to reel
    </button>
  {/if}
</div>
