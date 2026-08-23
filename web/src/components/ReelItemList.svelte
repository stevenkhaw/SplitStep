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

  // Shared by a normal release and a cancelled drag -- both end the drag the
  // same way locally (clear `dragging`, drop back to the `items` prop via
  // `shown`); only a normal release also commits. Same split ZoomBand draws
  // between its onPointerUp and onPointerCancel.
  function endDrag(): void {
    dragging = null
  }

  function onPointerUp(): void {
    if (dragging === null) return
    const moved = order
    const unchanged =
      moved.length === items.length &&
      moved.every((item, i) => item === items[i])
    endDrag()
    // A click on the handle is not a reorder. Committing anyway would mark
    // the reel dirty and demand a re-render for a gesture that changed
    // nothing.
    if (!unchanged) oncommit(moved)
  }

  // A pointercancel (app switch, an edge-swipe back gesture, a touch
  // scroll-vs-drag conflict, a context menu opening mid-touch) aborts the
  // gesture, not confirms it. Committing here would mark the reel dirty and
  // demand a re-render for a reorder the user never dropped -- so this only
  // runs the shared local cleanup and never calls `oncommit`.
  function onPointerCancel(): void {
    endDrag()
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

<svelte:window onpointermove={onPointerMove} onpointerup={onPointerUp} onpointercancel={onPointerCancel} />

<ul bind:this={list} class="divide-y divide-line">
  {#each shown as item, i (`${item.source_id}:${item.start_ms}:${item.end_ms}`)}
    <li
      data-reel-row
      class="flex items-center gap-3 py-2 {dragging === i ? 'opacity-50' : ''}"
    >
      <button
        data-drag-handle
        class="cursor-grab select-none px-2 font-data text-faint hover:text-fg"
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

      <span class="font-data text-data tabular-nums text-dim">
        {formatDuration(item.duration_ms)}
      </span>
      <span class="font-data text-data text-faint">
        source {String(item.source_idx).padStart(2, '0')}
      </span>

      {#if !item.clip_ready}
        <span class="rounded bg-danger/15 px-2 py-0.5 font-data text-caption text-danger">
          missing
        </span>
      {:else}
        <span class="rounded bg-surface-2 px-2 py-0.5 font-data text-data text-dim">
          ready
        </span>
      {/if}

      {#if item.rally === null}
        <!-- The rally this span came from is gone (a threshold sweep
             re-makes every rally). The clip is not: it is what the reel is
             actually made of, so this is badged, never dropped. -->
        <span class="rounded bg-surface-2 px-2 py-0.5 font-data text-data text-faint">
          orphan
        </span>
      {/if}

      <button
        data-remove
        class="ml-auto px-2 font-data text-data text-faint hover:text-danger"
        aria-label="Remove clip {i + 1}"
        onclick={() => onremove(item)}
      >✕</button>
    </li>
  {/each}
</ul>
