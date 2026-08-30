<script lang="ts">
  import ErrorNote from './ErrorNote.svelte'
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
  let error = $state<unknown>(null)
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
        if (!cancelled) error = e
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
        if (!cancelled) error = e
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

<div class="rounded-lg border border-line p-4">
  <div class="mb-3 flex items-baseline justify-between">
    <h2 class="text-body font-semibold">Add rallies</h2>
    <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors" onclick={onclose}>
      close
    </button>
  </div>

  {#if error}
    <ErrorNote {error} subject="session" />
  {:else}
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <select
        class="rounded border border-line bg-surface px-2 py-1 text-caption"
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
          class="rounded border px-2 py-1 font-data text-data
                 {filter === f
                   ? 'border-fg text-fg'
                   : 'border-line text-dim hover:bg-surface-2'} motion-safe:transition-colors"
          onclick={() => (filter = f as Filter)}
        >{f}</button>
      {/each}

      <button
        data-select-all
        class="ml-auto font-data text-data text-dim hover:text-fg
               disabled:opacity-40 motion-safe:transition-colors"
        disabled={selectable.length === 0}
        onclick={selectAll}
      >select all</button>
    </div>

    {#if loading}
      <p class="text-body text-dim">Loading…</p>
    {:else if shown.length === 0}
      <p class="text-body text-dim">Nothing matches this filter in that session.</p>
    {:else}
      <ul class="max-h-72 divide-y divide-line overflow-y-auto">
        {#each shown as r (r.id)}
          {@const inReel = alreadyIn.has(spanKey(r))}
          <li>
            <label class="flex items-center gap-3 py-2 text-body
                          {inReel ? 'text-faint' : ''}">
              <input
                type="checkbox"
                checked={inReel || checked.has(r.id)}
                disabled={inReel}
                onchange={() => toggle(r)}
              />
              <span class="font-data text-data tabular-nums">{formatTs(r.start_ms)}</span>
              <span class="font-data text-data tabular-nums text-dim">
                {formatDuration(r.end_ms - r.start_ms)}
              </span>
              {#if r.point}<span class="font-data text-data text-dim">P</span>{/if}
              {#if r.starred}<span class="font-data text-caption text-star">★</span>{/if}
              {#if inReel}
                <span class="ml-auto font-data text-data text-faint">in reel</span>
              {/if}
            </label>
          </li>
        {/each}
      </ul>
    {/if}

    <button
      data-add
      class="mt-3 rounded border border-line px-3 py-1.5 font-data text-data
             text-fg hover:bg-surface-2 disabled:cursor-not-allowed
             disabled:opacity-40 motion-safe:transition-colors"
      disabled={selected.length === 0}
      onclick={add}
    >
      Add {selected.length} to reel
    </button>
  {/if}
</div>
