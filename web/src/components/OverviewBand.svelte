<script lang="ts">
  import { msToFraction, sessionTimeline, toSessionMs } from '../lib/timeline'
  import { formatDuration } from '../lib/time'
  import type { Rally, Source } from '../lib/types'

  interface Props {
    rallies: Rally[]
    sources: Source[]
    currentId: string
    windowStartMs: number
    windowEndMs: number
    onpick: (rallyId: string) => void
  }

  let { rallies, sources, currentId, windowStartMs, windowEndMs, onpick }: Props = $props()

  const timeline = $derived(sessionTimeline(sources))

  function pos(rally: Rally) {
    const startMs = toSessionMs(sources, rally.source_id, rally.start_ms)
    const endMs = toSessionMs(sources, rally.source_id, rally.end_ms)
    const left = msToFraction(startMs, timeline.totalMs) * 100
    const width = Math.max(0.3, msToFraction(endMs - startMs, timeline.totalMs) * 100)
    return `left:${left}%;width:${width}%`
  }
</script>

<div class="relative h-7 overflow-hidden rounded bg-surface-2">
  {#each rallies as r (r.id)}
    <button
      type="button"
      class="absolute top-1 bottom-1 rounded-sm {r.starred
        ? 'bg-star'
        : 'bg-accent'} {r.id === currentId ? 'ring-2 ring-fg' : 'opacity-60'}"
      style={pos(r)}
      title={`rally ${r.idx}`}
      onclick={() => onpick(r.id)}
      aria-label={`rally ${r.idx}`}
      aria-current={r.id === currentId}
    ></button>
  {/each}

  {#each timeline.marks as m (m.sourceId)}
    {#if m.gapMs > 0}
      <div
        class="pointer-events-none absolute inset-y-0 w-px bg-faint"
        style={`left:${msToFraction(m.offsetMs, timeline.totalMs) * 100}%`}
        title={`+${formatDuration(m.gapMs)} break`}
      ></div>
    {/if}
  {/each}

  <div
    class="pointer-events-none absolute inset-y-0 border-2 border-fg/70 bg-fg/10"
    style={`left:${msToFraction(windowStartMs, timeline.totalMs) * 100}%;width:${
      msToFraction(windowEndMs - windowStartMs, timeline.totalMs) * 100
    }%`}
  ></div>
</div>
