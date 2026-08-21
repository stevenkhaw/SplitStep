<script lang="ts">
  import { api } from '../lib/api'
  import { dropIndex, moveItem } from '../lib/reorder'
  import { formatDuration } from '../lib/time'
  import type { ReelItem } from '../lib/types'

  interface Props {
    items: ReelItem[]
    /** Fired once, on release, with the reordered list. Never fired
     * mid-drag: a drag produces dozens of pointermoves and each one would
     * be a POST. Same split ZoomBand draws between onchange and oncommit,
     * except there is no local-only preview to fire here -- `order` below
     * IS the preview. */
    oncommit: (items: ReelItem[]) => void
    onremove: (item: ReelItem) => void
  }

  let { items, oncommit, onremove }: Props = $props()

  let list = $state<HTMLElement>()
  let dragging = $state<number | null>(null)
  // The live, in-progress order. Kept here rather than read back off the
  // `items` prop so the commit fired on release is exactly what this drag
  // produced, independent of whether the parent's prop echo has flushed.
  //
  // `$state.raw`, not `$state`: moveItem/dropIndex are pure and always hand
  // back a fresh array, so this only ever needs reactivity on reassignment,
  // never on mutating an element in place. Plain `$state` would deep-proxy
  // every ReelItem inside it, and endDrag's `item === items[i]` check below
  // compares against the *unproxied* objects straight off the `items` prop
  // -- a proxy and the value it wraps are never `===`, so a real no-op drag
  // would misread as a reorder and fire `oncommit` for nothing.
  let order = $state.raw<ReelItem[]>([])

  // What renders: the drag's working order while one is in progress, the
  // prop otherwise. The parent refetches after a commit, so this hands back
  // over cleanly once the server answers.
  const shown = $derived(dragging === null ? items : order)

  function rowMidpoints(): number[] {
    // The component's ONLY DOM read. Everything that decides where a row
    // lands is in lib/reorder.ts, because jsdom cannot help us here and
    // arithmetic in a .svelte file is untestable.
    if (!list) return []
    return [...list.querySelectorAll('[data-reel-row]')].map((el) => {
      const r = el.getBoundingClientRect()
      return r.top + r.height / 2
    })
  }

  function startDrag(index: number, e: PointerEvent): void {
    e.preventDefault()
    dragging = index
    order = [...items]
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }

  function onPointerMove(e: PointerEvent): void {
    if (dragging === null) return
    const to = dropIndex(e.clientY, rowMidpoints(), dragging)
    if (to === dragging) return
    order = moveItem(order, dragging, to)
    dragging = to
  }

  function endDrag(): void {
    if (dragging === null) return
    const moved = order
    const unchanged =
      moved.length === items.length &&
      moved.every((item, i) => item === items[i])
    dragging = null
    // A click on the handle is not a reorder. Committing anyway would mark
    // the reel dirty and demand a re-render for a gesture that changed
    // nothing.
    if (!unchanged) oncommit(moved)
  }

  function onHandleKey(index: number, e: KeyboardEvent): void {
    // Alt+Arrow, not bare Arrow: the handle is a button inside a scrolling
    // list, and swallowing plain arrows would break scrolling for keyboard
    // users. A pointer-only reorder is unreachable without a mouse, so this
    // is the accessible path, not a convenience.
    if (!e.altKey) return
    const to = e.key === 'ArrowUp' ? index - 1 : e.key === 'ArrowDown' ? index + 1 : null
    if (to === null || to < 0 || to >= items.length) return
    e.preventDefault()
    oncommit(moveItem(items, index, to))
  }
</script>

<svelte:window onpointermove={onPointerMove} onpointerup={endDrag} onpointercancel={endDrag} />

<ul bind:this={list} class="divide-y divide-neutral-800">
  {#each shown as item, i (`${item.source_id}:${item.start_ms}:${item.end_ms}`)}
    <li
      data-reel-row
      class="flex items-center gap-3 py-2 {dragging === i ? 'opacity-50' : ''}"
    >
      <button
        data-drag-handle
        class="cursor-grab select-none px-2 font-mono text-neutral-500 hover:text-neutral-200"
        aria-label="Reorder {i + 1}. Hold alt and press the up or down arrow."
        onpointerdown={(e) => startDrag(i, e)}
        onkeydown={(e) => onHandleKey(i, e)}
      >⠿</button>

      <!-- lazy: a 24-item reel would otherwise fire 24 on-demand frame
           extractions at once, each an ffmpeg call on the shared request
           thread pool. -->
      <img
        class="h-10 w-16 rounded bg-black object-cover"
        src={api.frameUrl(item.session_id, item.source_idx, item.start_ms)}
        alt=""
        loading="lazy"
      />

      <span class="font-mono text-xs tabular-nums text-neutral-300">
        {formatDuration(item.duration_ms)}
      </span>
      <span class="font-mono text-xs text-neutral-500">
        source {String(item.source_idx).padStart(2, '0')}
      </span>

      {#if !item.clip_ready}
        <span class="rounded bg-amber-500/15 px-2 py-0.5 font-mono text-xs text-amber-300">
          missing
        </span>
      {:else}
        <span class="rounded bg-neutral-800 px-2 py-0.5 font-mono text-xs text-neutral-400">
          ready
        </span>
      {/if}

      {#if item.rally === null}
        <!-- The rally this span came from is gone (a threshold sweep
             re-makes every rally). The clip is not: it is what the reel is
             actually made of, so this is badged, never dropped. -->
        <span class="rounded bg-neutral-800 px-2 py-0.5 font-mono text-xs text-neutral-500">
          orphan
        </span>
      {/if}

      <button
        data-remove
        class="ml-auto px-2 font-mono text-xs text-neutral-500 hover:text-red-300"
        aria-label="Remove clip {i + 1}"
        onclick={() => onremove(item)}
      >✕</button>
    </li>
  {/each}
</ul>
