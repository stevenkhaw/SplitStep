<script lang="ts">
  import { msToFraction, rallyBandLabel, sessionTimeline, toSessionMs } from '../lib/timeline'
  import { formatDuration } from '../lib/time'
  import type { ScoreRules } from '../lib/score'
  import type { Rally, Source } from '../lib/types'

  interface Props {
    rallies: Rally[]
    sources: Source[]
    currentId: string
    windowStartMs: number
    windowEndMs: number
    onpick: (rallyId: string) => void
    /** The session's scoring rules, or null when it tracks no score --
     *  which is also every session recorded before tracking existed, so
     *  this is optional and silent rather than defaulted. */
    rules?: ScoreRules | null
  }

  let { rallies, sources, currentId, windowStartMs, windowEndMs, onpick, rules = null }: Props =
    $props()

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
      class="absolute top-1 bottom-1 rounded-sm {r.rejected
        ? 'hatched bg-faint'
        : r.starred
          ? 'bg-star'
          : 'bg-dim'} {r.id === currentId ? 'ring-2 ring-fg' : r.rejected ? 'opacity-40' : 'opacity-60'}"
      style={pos(r)}
      title={rallyBandLabel(r, rules)}
      onclick={() => onpick(r.id)}
      aria-label={rallyBandLabel(r, rules)}
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

<style>
  /* A gap in the band used to read as "nothing here"; hatching says
     "something was here and a human said no". Token colour via var() so
     the pattern follows the theme. */
  .hatched {
    background-image: repeating-linear-gradient(
      135deg,
      transparent 0 3px,
      var(--color-bg) 3px 5px
    );
  }
</style>
