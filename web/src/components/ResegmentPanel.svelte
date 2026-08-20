<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { debounce } from '../lib/debounce'
  import { editedBoundaryCount, resegmentConfirmMessage } from '../lib/resegment'
  import type { Rally, Source } from '../lib/types'
  import ScoreCurve from './ScoreCurve.svelte'

  interface Props {
    sources: Source[]
    rallies: Rally[]
    onresegmented: () => void
  }

  let { sources, rallies, onresegmented }: Props = $props()

  const SCORE_DEBOUNCE_MS = 150
  // Mirrors SegmentParams.threshold in bootleg/detect/segment.py -- the
  // slider must start where a fresh detect run already landed, or the
  // first nudge silently resegments at a different value than the
  // rallies on screen were cut at.
  const DEFAULT_THRESHOLD = 0.45

  // A one-time snapshot, not a reactive read: the selected source is this
  // panel's own state once mounted, not something a later `sources` prop
  // update (e.g. after the resegment this panel itself triggers) should
  // silently override. `untrack` tells svelte-check this is intentional.
  let sourceId = $state(untrack(() => sources[0]?.id ?? ''))
  let threshold = $state(DEFAULT_THRESHOLD)
  let busy = $state(false)
  let lastCount = $state<number | null>(null)
  let error = $state<string | null>(null)
  let scores = $state<number[]>([])
  let scoreStepMs = $state(200)

  const source = $derived(sources.find((s) => s.id === sourceId))

  // Spec 6: stars and rejections carry across by overlap (server-side, see
  // replace_rallies), but a hand-dragged boundary does not. Losing one
  // silently would make the tuning loop feel hostile, so run() names the
  // cost before paying it.
  const editedCount = $derived(editedBoundaryCount(rallies, sourceId))

  function loadScores(id: string, th: number) {
    api
      .scores(id, th)
      .then((s) => {
        scores = s.scores
        scoreStepMs = s.step_ms
      })
      .catch(() => {
        scores = []
      })
  }

  // Fetched on mount and whenever the selected source changes -- never per
  // render. `threshold` is read untracked so this effect's only dependency
  // is `source`; threshold-driven refetches go through the debounced path
  // below instead (same split TimelineMode's preview slider uses).
  $effect(() => {
    if (source) loadScores(source.id, untrack(() => threshold))
  })

  // The scores endpoint costs real time (~83ms at one-hour scale: parsing
  // features.jsonl + scoring), so a slider drag must never fire it
  // per-pixel -- a burst of input events collapses into one request 150ms
  // after the drag goes quiet.
  const debouncedLoadScores = debounce(loadScores, SCORE_DEBOUNCE_MS)
  $effect(() => {
    return () => debouncedLoadScores.cancel()
  })

  function onThresholdInput(value: number) {
    // The dashed threshold line moves immediately -- it's pure local math,
    // no network (see ScoreCurve). Only the score refetch is debounced.
    threshold = value
    if (source) debouncedLoadScores(source.id, value)
  }

  function onSourceChange(id: string) {
    // Otherwise the previous source's "N rallies at threshold ..." result
    // (or error) keeps showing after switching sources, which reads as if
    // it describes the newly selected one.
    sourceId = id
    lastCount = null
    error = null
  }

  // The actual re-segmentation is deliberately NOT wired to the slider or
  // its debounce: it discards hand-edited boundaries (see editedCount), and
  // firing it automatically on every settled slider position -- even
  // debounced -- would mean paying that cost, and re-showing the
  // confirmation below, on every pause during a drag. It only runs on this
  // explicit, once-per-click action.
  async function run() {
    if (!source) return
    if (editedCount > 0) {
      const ok = confirm(resegmentConfirmMessage(editedCount))
      if (!ok) return
    }
    busy = true
    error = null
    try {
      const res = await api.resegment(source.id, threshold)
      lastCount = res.count ?? null
      onresegmented()
    } catch (e) {
      error = String(e)
    } finally {
      busy = false
    }
  }
</script>

<section class="mt-6 rounded-lg border border-neutral-800 p-4">
  <h2 class="text-sm font-semibold">Re-segment</h2>
  <p class="mt-1 text-xs text-neutral-400">
    Runs over cached features — no GPU. Stars and rejections carry across by overlap;
    hand-edited boundaries do not.
  </p>

  <div class="mt-3 flex items-center gap-3">
    <select
      value={sourceId}
      onchange={(e) => onSourceChange(e.currentTarget.value)}
      class="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm"
      aria-label="source to re-segment"
    >
      {#each sources as s (s.id)}
        <option value={s.id}>source {s.idx}</option>
      {/each}
    </select>

    <input
      type="range"
      min="0.05"
      max="0.95"
      step="0.01"
      value={threshold}
      oninput={(e) => onThresholdInput(Number(e.currentTarget.value))}
      class="flex-1"
      aria-label="detector threshold"
    />
    <span class="w-12 font-mono text-sm">{threshold.toFixed(2)}</span>

    <button
      class="rounded bg-blue-600 px-3 py-1 text-sm disabled:opacity-40"
      onclick={run}
      disabled={busy || !source}
    >
      {busy ? 'working…' : 'Re-segment'}
    </button>
  </div>

  {#if source}
    <div class="mt-3">
      <ScoreCurve {scores} {threshold} stepMs={scoreStepMs} windowStartMs={0} windowEndMs={source.duration_ms} />
      <p class="mt-1 font-mono text-[11px] text-neutral-500">
        detector score for the whole source — dashed line is the threshold above
      </p>
    </div>
  {/if}

  {#if editedCount > 0}
    <p class="mt-2 text-xs text-amber-300">
      {editedCount} hand-edited boundar{editedCount === 1 ? 'y' : 'ies'} on this source will be
      discarded if you re-segment.
    </p>
  {/if}
  {#if lastCount !== null}
    <p class="mt-2 font-mono text-xs text-neutral-400">
      {lastCount} rallies at threshold {threshold.toFixed(2)}
    </p>
  {/if}
  {#if error}
    <p class="mt-2 text-xs text-red-300">{error}</p>
  {/if}
</section>
