<script lang="ts">
  import { describeApiError } from '../lib/errors'
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { debounce } from '../lib/debounce'
  import {
    STALE_REGION_WARNING,
    editedBoundaryCount,
    recordedThresholdNote,
    redetectConfirmMessage,
    resegmentConfirmMessage,
    resegmentLossPhrase,
    seedThreshold,
    splitCount,
    staleRegionSources,
  } from '../lib/resegment'
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
  // Seeded from the source's own recorded threshold -- the number its
  // rallies were actually cut at (migration 015) -- and null only when no
  // segmentation ever recorded one. In that case the first /scores response
  // supplies the per-source profile default instead: the two camera profiles
  // put the threshold on different scales (0.25 subject, 0.45 pair), so a
  // constant here is wrong for half of all sources.
  //
  // The recorded value takes priority over that default precisely because
  // the default is a fact about the DETECTOR and this slider is a claim
  // about the RALLIES ON SCREEN. Source 2026-09-16/01 was re-segmented at
  // 0.15 and reopened reading 0.25, over rallies carrying confidence down to
  // 0.176 -- the readout was stating something false about the list beside
  // it, which is worse than having forgotten.
  let threshold = $state<number | null>(seedThreshold(untrack(() => sources[0])))
  let busy = $state(false)
  let lastCount = $state<number | null>(null)
  let error = $state<unknown>(null)
  // The stale-region banner renders outside the collapse (see the markup),
  // so a failed detect queued from it cannot report into `error` above --
  // that line is inside the <details> and would be swallowed by the very
  // collapse this banner exists to get out from behind.
  let detectError = $state<unknown>(null)
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
  const splits = $derived(splitCount(rallies, sourceId))

  // This whole panel replays cached features, and the play region is baked
  // into those features when they are built -- so a region assigned after
  // the last detect is invisible to every threshold here. Without this the
  // slider is silently a no-op with respect to the change the reviewer just
  // made, which reads as re-segment being broken rather than as the wrong
  // tool (that is exactly how it was reported).
  //
  // Every stale source, not the selected one: the selector sits inside the
  // collapse and the banner does not, so scoping to it would go quiet again
  // for any source nobody has picked. `sources` is the only input -- two
  // timestamps per row, no fetch (see staleRegionSources).
  const staleSources = $derived(staleRegionSources(sources))

  // What the slider's starting position actually means, said out loud. The
  // number alone cannot distinguish "these rallies were cut at 0.25" from
  // "nobody knows, so here is the default" -- and confusing those two is the
  // bug. Reads the live `sources` prop, not the mount-time snapshot, so it
  // updates to the freshly recorded value after this panel's own re-segment.
  const thresholdNote = $derived(recordedThresholdNote(source))

  async function redetect(target: Source) {
    if (busy) return
    // The same sentence the quad editor's own re-detect button asks --
    // shared, because the two queue the same job at the same cost.
    if (!confirm(redetectConfirmMessage())) return
    busy = true
    detectError = null
    try {
      await api.detectSource(target.id)
      // Not `lastCount`: nothing has been re-segmented. The jobs badge is
      // what tracks a detect, here as everywhere else.
      lastCount = null
    } catch (e) {
      detectError = e
    } finally {
      busy = false
    }
  }

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
  // A genuine switch to a different source re-seeds `threshold` from that
  // source first -- its recorded value, or null -- so the call below sends
  // the recorded one or omits it and asks the API to resolve *that*
  // source's own profile default. First call is per source, not just once
  // per mount. Carrying over the *previous* source's value here would be
  // sent as an explicit override, which the server just echoes back (see
  // loadScores' comment above), silently wrong-scale whenever the two
  // sources sit on different camera-view profiles.
  //
  // Note the two ways a threshold reaches this call, and that they are not
  // interchangeable: a recorded value goes out explicitly (so loadScores
  // does not overwrite it with the echoed default), a null goes out as
  // "resolve it for me". Seeding a recorded value also removes the null
  // window entirely for that source -- the slider is live on the first
  // frame, with no round trip to wait on.
  $effect(() => {
    // Reading `open` makes this effect fire on first expand, so the panel
    // fetches when it is actually looked at. Re-collapsing just re-runs it
    // into this guard -- nothing is torn down, and reopening refetches
    // against whatever the source looks like by then.
    if (!open) return
    if (!source) return
    // A genuine switch re-seeds from the NEW source's recorded threshold, per
    // source -- two sources' recorded values differ the same way their
    // profile defaults do, so carrying one across would state the wrong
    // number about the other's rallies. Null when it has none, which sends
    // the call below back to asking for that source's own profile default.
    if (source.id !== scoredSourceId) threshold = seedThreshold(source)
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
    if (editedCount > 0 || splits > 0) {
      const ok = confirm(resegmentConfirmMessage(editedCount, splits))
      if (!ok) return
    }
    busy = true
    error = null
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

<div class="mt-6 space-y-3">
  <!--
    Outside the <details>, and that placement is the whole fix.

    This warning is one of exactly two places the app says that a changed
    play region needs a full re-detect rather than a re-segment (CLAUDE.md,
    "Play region") -- and it used to render inside the panel below, which
    ships collapsed. So the one sentence explaining why a freshly assigned
    region is being ignored was visible only to a reviewer who had already
    opened the threshold-tuning panel, which is to say: only to someone
    about to make the exact mistake it warns against. Reported on source
    2026-09-16/01, region assigned 17:49 against features from 03:29 --
    re-segmented, nothing changed, nothing on screen said why.

    Above the collapse rather than on the <summary>: a summary is a toggle,
    so a button inside one has to fight its click (every click in a summary
    toggles the details), and a sentence plus a filled button is more than a
    one-line disclosure row can carry anyway. CLAUDE.md records that this
    affordance as a text link was already missed once -- shrinking it to fit
    a summary would be walking back into that.

    The collapse itself stays closed by default: it gates the /scores fetch,
    which parses the whole of features.jsonl, and this banner costs two
    timestamp comparisons off the `sources` prop -- no fetch, no reason to
    open anything.
  -->
  {#if staleSources.length > 0}
    <!-- Outlined in `fg`, not `danger`: nothing has failed. The region was
         assigned correctly and the features are honestly out of date, so
         this is a call to action, and the app's treatment for one of those
         is outline and weight, never a colour. `bg-surface` because the
         caption below is `dim`, which may not sit on bare court. -->
    <section
      class="space-y-3 rounded-lg border border-fg bg-surface p-4"
      aria-label="play region changed since the last detect"
    >
      {#each staleSources as s (s.id)}
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="min-w-0">
            <p class="text-body text-fg">
              <span class="font-data">source {s.idx}</span> — {STALE_REGION_WARNING}
            </p>
            <p class="mt-1 text-caption text-dim">
              The region is applied when features are built, so only a full detection picks it up.
            </p>
          </div>
          <button
            class="shrink-0 rounded-lg bg-fg px-4 py-2 text-body font-semibold text-bg
                   hover:bg-fg/90 disabled:opacity-40 motion-safe:transition-colors"
            onclick={() => redetect(s)}
            disabled={busy}
          >
            {busy ? 'working…' : 'Run detection'}
          </button>
        </div>
      {/each}
      {#if detectError}
        <p class="mt-2 text-caption text-danger">{describeApiError(detectError, 'source').message}</p>
      {/if}
    </section>
  {/if}

  <details bind:open class="rounded-lg border border-line">
    <summary class="cursor-pointer select-none p-4 text-body font-semibold">Re-segment</summary>

    <div class="px-4 pb-4">
      <p class="text-caption text-dim">
        Runs over cached features — no GPU. Stars and rejections carry across by overlap;
        hand-edited boundaries do not.
      </p>

      <div class="mt-3 flex items-center gap-3">
        <select
          value={sourceId}
          onchange={(e) => onSourceChange(e.currentTarget.value)}
          class="rounded border border-line bg-surface px-2 py-1 text-body"
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
        <span class="w-12 font-data text-body">{threshold === null ? '…' : threshold.toFixed(2)}</span>

        <button
          class="rounded bg-fg px-3 py-1 text-body font-medium text-bg hover:bg-fg/90 disabled:opacity-40 motion-safe:transition-colors"
          onclick={run}
          disabled={busy || !source || threshold === null}
        >
          {busy ? 'working…' : 'Re-segment'}
        </button>
      </div>

      <!-- Under the slider, because it explains where the thumb is sitting.
           `dim` on a `surface` card (the panel's own border/ground), never on
           bare court. The number is `font-data` like every other threshold,
           timecode and confidence in the app -- it is read against the mono
           readout a few pixels above it, and two spellings of one number is
           the disagreement this whole change is about. -->
      {#if thresholdNote}
        <p class="mt-2 text-caption text-dim">
          {thresholdNote.lead}{#if thresholdNote.value !== null}
            <span class="font-data">{thresholdNote.value}</span>.{/if}
        </p>
      {/if}

      {#if source && threshold !== null}
        <div class="mt-3">
          <ScoreCurve {scores} {threshold} stepMs={scoreStepMs} windowStartMs={0} windowEndMs={source.duration_ms} />
          <p class="mt-1 font-data text-caption text-faint">
            detector score for the whole source — dashed line is the threshold above
          </p>
        </div>
      {/if}

      {#if editedCount > 0 || splits > 0}
        <p class="mt-2 text-caption text-danger">
          Re-segmenting will discard {resegmentLossPhrase(editedCount, splits)} on this source.
        </p>
      {/if}
      {#if lastCount !== null && threshold !== null}
        <p class="mt-2 font-data text-data text-dim">
          {lastCount} rallies at threshold {threshold.toFixed(2)}
        </p>
      {/if}
      {#if error}
        <p class="mt-2 text-caption text-danger">{describeApiError(error, 'source').message}</p>
      {/if}
    </div>
  </details>
</div>
