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

  // A one-time snapshot, not a reactive read: the selected source is this
  // panel's own state once mounted, not something a later `sources` prop
  // update (e.g. after the resegment this panel itself triggers) should
  // silently override. `untrack` tells svelte-check this is intentional.
  let sourceId = $state(untrack(() => sources[0]?.id ?? ''))
  // Null until the first /scores response supplies it. The two camera
  // profiles put the threshold on different scales (0.25 subject, 0.45 pair),
  // so a constant here is wrong for half of all sources -- the API resolves
  // it per source and this is where that answer lands.
  let threshold = $state<number | null>(null)
  let busy = $state(false)
  let lastCount = $state<number | null>(null)
  let error = $state<string | null>(null)
  // Collapsed by default: this is the threshold-tuning loop, opened
  // deliberately, not something a review pass touches. It also gates the
  // /scores fetch below -- that call parses the whole of features.jsonl, and
  // paying it on every session load for a panel nobody opened is the cost
  // this collapse actually removes.
  let open = $state(false)
  let scores = $state<number[]>([])
  let scoreStepMs = $state(200)

  const source = $derived(sources.find((s) => s.id === sourceId))

  // Spec 6: stars and rejections carry across by overlap (server-side, see
  // replace_rallies), but a hand-dragged boundary does not. Losing one
  // silently would make the tuning loop feel hostile, so run() names the
  // cost before paying it.
  const editedCount = $derived(editedBoundaryCount(rallies, sourceId))

  function loadScores(id: string, th: number | null) {
    api
      .scores(id, th ?? undefined)
      .then((s) => {
        // /scores' cost scales with features.jsonl's length, so responses
        // are not guaranteed to land in request order: switch from a long
        // source to a short one and the long one's response routinely
        // arrives second. scoredSourceId is reassigned synchronously before
        // this call goes out (see the $effect below), so by the time any
        // response lands it names whichever source is *currently* selected
        // -- a mismatch means a later switch already superseded this one.
        if (id !== scoredSourceId) return
        scores = s.scores
        scoreStepMs = s.step_ms
        // Only adopt the server's echoed threshold when we asked it to
        // resolve the profile default (th === null). Writing it back
        // unconditionally -- including for an explicit slider value we
        // already applied locally in onThresholdInput -- is itself a race:
        // a debounced response for an in-flight drag can land after the
        // user has moved the slider further and snap the thumb backward.
        if (th === null) threshold = s.threshold
      })
      .catch(() => {
        if (id !== scoredSourceId) return
        scores = []
      })
  }

  // Plain bookkeeping, not $state: reading/writing it must never itself
  // create or satisfy a reactive dependency. It exists only so the effect
  // below can tell a genuine source switch apart from `source` merely
  // getting a new object identity for the *same* id -- which happens after
  // this panel's own resegment call re-fetches the session and Session.svelte
  // hands `sources` back as a brand-new array (see Session.svelte's
  // `onresegmented`). Comparing derived-`source` reference identity would
  // treat that incidental churn as a switch too and needlessly null out
  // (and re-fetch) a threshold the user is still looking at.
  let scoredSourceId: string | undefined

  // Fetched on mount and whenever the selected source changes -- never per
  // render. `threshold` is read untracked so this effect's only dependency
  // is `source`; threshold-driven refetches go through the debounced path
  // below instead (same split TimelineMode's preview slider uses).
  //
  // A genuine switch to a different source resets `threshold` to null
  // first, so the call below omits it and asks the API to resolve *that*
  // source's own profile default -- first call is per source, not just
  // once per mount. Carrying over the previous source's numeric value here
  // would be sent as an explicit override, which the server just echoes
  // back (see loadScores' comment above), silently wrong-scale whenever
  // the two sources sit on different camera-view profiles.
  $effect(() => {
    // Reading `open` makes this effect fire on first expand, so the panel
    // fetches when it is actually looked at. Re-collapsing just re-runs it
    // into this guard -- nothing is torn down, and reopening refetches
    // against whatever the source looks like by then.
    if (!open) return
    if (!source) return
    if (source.id !== scoredSourceId) threshold = null
    scoredSourceId = source.id
    loadScores(source.id, untrack(() => threshold))
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
    // threshold is null only in the gap before the first /scores response
    // lands; the button is disabled for that whole window (see template),
    // so this is a type-narrowing guard against a stale click racing the
    // response, not a path expected to fire in practice.
    if (!source || threshold === null) return
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

<details bind:open class="mt-6 rounded-lg border border-neutral-800">
  <summary class="cursor-pointer select-none p-4 text-sm font-semibold">Re-segment</summary>

  <div class="px-4 pb-4">
    <p class="text-xs text-neutral-400">
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
        value={threshold ?? 0.05}
        oninput={(e) => onThresholdInput(Number(e.currentTarget.value))}
        disabled={threshold === null}
        class="flex-1 disabled:opacity-40"
        aria-label="detector threshold"
      />
      <span class="w-12 font-mono text-sm">{threshold === null ? '…' : threshold.toFixed(2)}</span>

      <button
        class="rounded bg-blue-600 px-3 py-1 text-sm disabled:opacity-40"
        onclick={run}
        disabled={busy || !source || threshold === null}
      >
        {busy ? 'working…' : 'Re-segment'}
      </button>
    </div>

    {#if source && threshold !== null}
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
    {#if lastCount !== null && threshold !== null}
      <p class="mt-2 font-mono text-xs text-neutral-400">
        {lastCount} rallies at threshold {threshold.toFixed(2)}
      </p>
    {/if}
    {#if error}
      <p class="mt-2 text-xs text-red-300">{error}</p>
    {/if}
  </div>
</details>
