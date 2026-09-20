<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { debounce } from '../lib/debounce'
  import { describeApiError } from '../lib/errors'
  import { quadPolygonPoints } from '../lib/quad'
  import {
    NO_CURVE_NOTE,
    type RegionState,
    detectionActionLabel,
    detectionActionNote,
    editedBoundaryCount,
    hasFeatures,
    planDetection,
    recordedThresholdNote,
    redetectConfirmMessage,
    regionAssignedNow,
    regionInEffect,
    regionStateLabel,
    resegmentConfirmMessage,
    resegmentLossPhrase,
    seedThreshold,
    splitCount,
  } from '../lib/resegment'
  import type { Rally, Source } from '../lib/types'
  import ScoreCurve from './ScoreCurve.svelte'

  interface Props {
    source: Source
    rallies: Rally[]
    onresegmented: () => void
  }

  let { source, rallies, onresegmented }: Props = $props()

  const SCORE_DEBOUNCE_MS = 150
  // The quad thumbnails. Wide enough that a dragged corner is visibly a
  // different shape at a glance, small enough to sit on one line beside
  // its own label -- this is a comparison, not a canvas (that is
  // QuadEditor, which owns the drag and pays for a real frame).
  const QUAD_W = 44
  const QUAD_H = 26

  // One panel per source now, keyed by id in Session.svelte, so there is no
  // selector and no source switch to race. That deletes a whole class of
  // bug the previous single panel carried: a /scores response landing after
  // the reviewer had moved to another video, a threshold carried across two
  // sources whose camera profiles put it on different scales, and a
  // stale-region warning that could only speak about whichever source
  // happened to be picked. A source's own panel cannot be looking at
  // another source.

  // Seeded from the source's own recorded threshold (migration 015) -- the
  // number its rallies were actually cut at -- and null only when nothing
  // recorded one. A recorded value means the slider is live on the first
  // frame with no round trip; null is answered by the first /scores
  // response, which resolves this source's profile default (0.25 subject,
  // 0.45 pair -- a constant here would be wrong for half of all sources).
  //
  // `untrack` because this is a seed, not a mirror: the panel owns the
  // slider once mounted, and the prop churn from its own re-segment
  // (Session refetches the session and hands back fresh Source objects)
  // must not yank a value the reviewer is still looking at.
  let threshold = $state<number | null>(seedThreshold(untrack(() => source)))
  let busy = $state(false)
  let lastCount = $state<number | null>(null)
  let queued = $state<string | null>(null)
  let error = $state<unknown>(null)
  // Collapsed by default, and that collapse is load-bearing for one reason
  // only: it gates the /scores fetch below, which parses the whole of
  // features.jsonl. Nothing the reviewer needs in order to DECIDE is
  // inside it -- both regions, the threshold the rallies were cut at, the
  // action and its cost all render above. That split is the fix for the
  // bug shipped this morning, where the stale-region information sat
  // behind a disclosure triangle that ships closed, so the one sentence
  // explaining why a freshly assigned region was being ignored reached
  // only a reviewer already doing the thing it warns against.
  let open = $state(false)
  let scores = $state<number[]>([])
  let scoreStepMs = $state(200)

  // One plan, read by the button, its label and its sentence, so the three
  // cannot describe different actions. The draft region is whatever is
  // assigned to the source right now: QuadEditor writes it, this panel is
  // where it costs something.
  const plan = $derived(
    planDetection(source, { region: source.court_preset_points, threshold }),
  )
  const inEffect = $derived(regionInEffect(source))
  const assigned = $derived(regionAssignedNow(source))

  // Spec 6: stars and rejections carry across by overlap (server side, see
  // replace_rallies), but a hand-dragged boundary and a hand-made rally do
  // not. run() names that cost before paying it.
  const editedCount = $derived(editedBoundaryCount(rallies, source.id))
  const splits = $derived(splitCount(rallies, source.id))
  // One phrase, shared with the click-time confirm above, so the warning
  // the reviewer reads before deciding and the dialog they read while
  // deciding cannot name different costs.
  const lossPhrase = $derived(resegmentLossPhrase(editedCount, splits))

  // What the slider's position means, said out loud where the slider is.
  // The always-visible header says what the RALLIES were cut at
  // (`plan.cutAt`); this says what the thumb under your finger is, which
  // is a different claim whenever the two differ -- and identical copy in
  // both places would hide exactly that.
  const thresholdNote = $derived(recordedThresholdNote(source))

  function loadScores(id: string, th: number | null) {
    api
      .scores(id, th ?? undefined)
      .then((s) => {
        scores = s.scores
        scoreStepMs = s.step_ms
        // Only adopt the server's echoed threshold when we asked it to
        // resolve the profile default (th === null). Writing it back for a
        // value we already applied locally is a race: a debounced response
        // for an in-flight drag can land after the thumb has moved on and
        // snap it backward.
        if (th === null) threshold = s.threshold
      })
      .catch(() => {
        scores = []
      })
  }

  // Fires on first expand and not before, so an unopened panel costs
  // nothing. Re-collapsing runs it back into the guard; reopening refetches
  // against whatever the source looks like by then.
  //
  // Guarded on features too: /scores answers 409 without them, so asking
  // would render an error the reviewer can do nothing about, over a source
  // whose honest state is "nothing has looked yet" (see NO_CURVE_NOTE).
  $effect(() => {
    if (!open) return
    if (!hasFeatures(source)) return
    loadScores(source.id, untrack(() => threshold))
  })

  // ~83ms at one-hour scale (parsing features.jsonl + scoring), so a drag
  // must never fire it per-pixel: a burst collapses into one request 150ms
  // after the drag goes quiet.
  const debouncedLoadScores = debounce(loadScores, SCORE_DEBOUNCE_MS)
  $effect(() => {
    return () => debouncedLoadScores.cancel()
  })

  function onThresholdInput(value: number) {
    // The dashed line moves immediately -- pure local math, no network.
    // Only the refetch is debounced.
    threshold = value
    debouncedLoadScores(source.id, value)
  }

  /**
   * The one action, whichever one the plan says it is.
   *
   * Deliberately not wired to the slider or its debounce. Both branches
   * discard hand-edited boundaries, and firing on every settled slider
   * position would mean paying that cost -- and re-asking the confirmation
   * -- on every pause during a drag.
   */
  async function apply() {
    if (busy || plan.action === 'none') return
    if (plan.action === 'redetect') {
      if (!confirm(redetectConfirmMessage())) return
      busy = true
      error = null
      try {
        // The threshold rides along with the expensive run, which is the
        // whole point of taking one at POST /detect (da51585): a detect
        // re-segments at the end, so without this the fifteen-minute run
        // silently re-cuts at the profile default and throws away the
        // number the reviewer had chosen. `?? undefined` so an unresolved
        // slider omits the key entirely rather than sending null.
        await api.detectSource(source.id, threshold ?? undefined)
        // Not `lastCount`: nothing has been re-segmented yet. The jobs
        // badge is what tracks a detect, here as everywhere else.
        lastCount = null
        queued = 'Detection queued — the jobs badge above tracks it.'
      } catch (e) {
        error = e
      } finally {
        busy = false
      }
      return
    }

    // `resegment`. The plan only reaches it with a non-null threshold, so
    // this is a type narrowing against a stale click, not an expected path.
    if (threshold === null) return
    if (editedCount > 0 || splits > 0) {
      if (!confirm(resegmentConfirmMessage(editedCount, splits))) return
    }
    busy = true
    error = null
    queued = null
    try {
      const res = await api.resegment(source.id, threshold)
      lastCount = res.count ?? null
      onresegmented()
    } catch (e) {
      error = e
    } finally {
      busy = false
    }
  }
</script>

<!--
  One region, drawn and named. The shape is `aria-hidden` and carries no
  role: the words beside it already say which of the four states this is,
  and a screen reader gains nothing from a polygon it cannot compare.
-->
{#snippet regionCard(role: string, title: string, sub: string, state: RegionState)}
  <div data-role={role} class="rounded-lg border border-line bg-surface-2 p-3">
    <p class="text-caption text-dim">{title}</p>
    <div class="mt-1 flex items-center gap-2">
      {#if state.kind === 'quad'}
        <svg
          viewBox="0 0 {QUAD_W} {QUAD_H}"
          width={QUAD_W}
          height={QUAD_H}
          class="shrink-0"
          aria-hidden="true"
          focusable="false"
        >
          <rect x="0.5" y="0.5" width={QUAD_W - 1} height={QUAD_H - 1}
                fill="none" stroke="currentColor" stroke-width="1" class="text-line" />
          <polygon
            points={quadPolygonPoints(state.points, QUAD_W, QUAD_H)}
            fill="currentColor"
            fill-opacity="0.25"
            stroke="currentColor"
            stroke-width="1"
            class="text-fg"
          />
        </svg>
      {/if}
      <p class="font-data text-data text-fg">{regionStateLabel(state)}</p>
    </div>
    <p class="mt-1 text-caption text-dim">{sub}</p>
  </div>
{/snippet}

<!--
  `border-fg` while an action is live and `border-line` otherwise: the app
  has no accent, so a call to action is outline and weight (CLAUDE.md,
  Design tokens). Never `danger` -- a region the reviewer assigned
  correctly, over features that are honestly older than it, is work to do
  and not a failure. `bg-surface` because everything secondary in here is
  `dim` or `faint`, which may not sit on bare court.
-->
<section
  class="mt-6 rounded-lg border bg-surface p-4 {plan.action === 'none'
    ? 'border-line'
    : 'border-fg'}"
  aria-label="detection for source {source.idx}"
>
  <div class="flex flex-wrap items-start justify-between gap-3">
    <div class="min-w-0">
      <h2 class="text-title font-semibold">Detection — source {source.idx}</h2>
      <!-- The threshold the rallies BELOW were cut at, and "unknown" in
           words when nothing recorded one. Naming the profile default here
           would restate the original bug in prose: 0.25 is a fact about the
           detector's subject profile, not about this list of rallies.
           `font-data` because it carries a threshold, like every other
           number in the app. -->
      <p data-role="cut-at" class="mt-1 font-data text-data text-dim">
        current rallies — {plan.cutAt}
      </p>
    </div>

    <button
      data-role="detection-primary"
      class="shrink-0 rounded-lg bg-fg px-4 py-2 text-body font-semibold text-bg
             hover:bg-fg/90 disabled:opacity-40 motion-safe:transition-colors"
      onclick={apply}
      disabled={busy || plan.action === 'none'}
    >
      {busy ? 'working…' : detectionActionLabel(plan.action)}
    </button>
  </div>

  <p data-role="action-note" class="mt-2 max-w-3xl text-caption text-dim">
    {detectionActionNote(plan)}
  </p>

  <!--
    Both regions, side by side, outside the collapse. "How do i know what
    play region it is using?" had no answer anywhere in the app; a
    timestamp comparison was the closest thing, and it was wrong -- the
    wizard stamps preset_assigned_at on every save, so re-confirming the
    same four corners announced a change and offered fifteen minutes of
    GPU. Migration 016 records the corners the features were built under,
    and showing both is a stronger answer than any warning: the reviewer
    sees whether they differ instead of being told.
  -->
  <div class="mt-3 grid gap-3 sm:grid-cols-2">
    {@render regionCard(
      'region-in-effect',
      'Region in effect',
      'what the cached features were built under',
      inEffect,
    )}
    {@render regionCard(
      'region-assigned',
      'Region assigned now',
      'what the next detection will use',
      assigned,
    )}
  </div>

  <details bind:open class="mt-3 rounded-lg border border-line">
    <summary class="cursor-pointer select-none px-3 py-2 text-body font-semibold">
      Threshold
    </summary>
    <div class="px-3 pb-3">
      {#if !plan.curveAvailable}
        <!-- Not an empty chart and not a slider either: see NO_CURVE_NOTE.
             The threshold's scale depends on a camera profile detection
             works out from features that do not exist yet. -->
        <p class="text-caption text-dim">{NO_CURVE_NOTE}</p>
      {:else}
        <div class="flex items-center gap-3">
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
          <span class="w-12 font-data text-body">
            {threshold === null ? '…' : threshold.toFixed(2)}
          </span>
        </div>

        {#if thresholdNote}
          <p class="mt-2 text-caption text-dim">
            {thresholdNote.lead}{#if thresholdNote.value !== null}
              <span class="font-data">{thresholdNote.value}</span>.{/if}
          </p>
        {/if}

        {#if threshold !== null}
          <div class="mt-3">
            <ScoreCurve
              {scores}
              {threshold}
              stepMs={scoreStepMs}
              windowStartMs={0}
              windowEndMs={source.duration_ms}
            />
            <p class="mt-1 font-data text-caption text-faint">
              detector score for the whole source — dashed line is the threshold above
            </p>
          </div>
        {/if}
      {/if}
    </div>
  </details>

  {#if editedCount > 0 || splits > 0}
    <!-- `danger` here and only here in this panel: this names data that
         will be destroyed, which is the one thing in the panel that is
         actually a loss rather than a cost. -->
    <p class="mt-2 text-caption text-danger">
      Re-cutting this video discards {lossPhrase} on it.
    </p>
  {/if}
  {#if lastCount !== null && threshold !== null}
    <p class="mt-2 font-data text-data text-dim">
      {lastCount} rallies at threshold {threshold.toFixed(2)}
    </p>
  {/if}
  {#if queued}
    <p class="mt-2 font-data text-data text-fg">{queued}</p>
  {/if}
  {#if error}
    <p class="mt-2 text-caption text-danger">{describeApiError(error, 'source').message}</p>
  {/if}
</section>
