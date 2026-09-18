<script lang="ts">
  import { describeApiError } from '../lib/errors'
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { redetectConfirmMessage } from '../lib/resegment'
  import { DEFAULT_QUAD_POINTS, assignedPresetLabel, clonePoints, defaultPresetName } from '../lib/quad'
  import { DEFAULT_SCRUB_MS, clamp, formatTs, lastSafeFrameMs } from '../lib/time'
  import type { Preset, Source } from '../lib/types'
  import QuadCanvas from './QuadCanvas.svelte'

  interface Props {
    sessionId: string
    sources: Source[]
    onassigned: () => void
  }

  let { sessionId, sources, onassigned }: Props = $props()

  // A one-time snapshot, not a reactive read -- same reasoning as
  // ResegmentPanel's `sourceId`: this panel owns which source it is
  // editing once mounted, and a later `sources` prop update (e.g. after
  // the assignment this panel itself triggers) must not silently swap the
  // selection out from under a mid-drag user. `untrack` tells svelte-check
  // this is intentional (state_referenced_locally).
  let sourceId = $state(untrack(() => sources[0]?.id ?? ''))
  let source = $derived(sources.find((s) => s.id === sourceId))

  /**
   * Phone footage routinely opens on a black frame -- the record button is
   * hit before the phone is propped against the fence -- and a black frame
   * is useless for dragging a play region over. Open a little way in
   * instead, clamped so a clip shorter than that still lands on a real
   * frame rather than past its end.
   */
  function openAt(s: Source | undefined): number {
    if (!s) return 0
    return Math.min(DEFAULT_SCRUB_MS, lastSafeFrameMs(s.duration_ms, s.fps))
  }

  // The timestamp the displayed frame was extracted at. Separate from
  // `scrubMs` on purpose: every distinct value here costs one ffmpeg
  // extraction on the server, so a slider drag updates the readout
  // continuously but only commits (and fetches) on release.
  let frameMs = $state(untrack(() => openAt(sources[0])))
  let scrubMs = $state(untrack(() => openAt(sources[0])))

  const maxMs = $derived(source ? lastSafeFrameMs(source.duration_ms, source.fps) : 0)

  function seek(ms: number): void {
    const next = Math.round(clamp(ms, 0, maxMs))
    scrubMs = next
    frameMs = next
  }

  let points = $state<[number, number][]>(clonePoints(DEFAULT_QUAD_POINTS))
  let name = $state('')
  let presets = $state<Preset[]>([])
  let status = $state<string | null>(null)
  let error = $state<unknown>(null)
  // Guards both `save` and `assignExisting`: without it, a double-click on
  // "Save & assign" fires createPreset twice before the first request's
  // response lands, creating two identically-shaped presets under
  // (usually) the same name -- same double-submit class ResegmentPanel's
  // `busy` already guards against.
  let busy = $state(false)
  // Collapsed by default. This panel is a once-per-source setup step -- the
  // wizard already walked the user through it at ingest -- so on a session
  // you are reviewing it is chrome between you and the queue. Collapsed also
  // means QuadCanvas never mounts (see the `{#if open}` in the template),
  // and its frame <img> is one server-side ffmpeg extraction per distinct
  // timestamp, so an unopened panel now costs nothing instead of an encode.
  let open = $state(false)

  async function refreshPresets(): Promise<void> {
    try {
      presets = await api.listPresets()
    } catch (e) {
      error = e
    }
  }

  $effect(() => {
    refreshPresets()
  })

  const assignedLabel = $derived(assignedPresetLabel(source?.court_preset_id ?? null, presets))

  function onSourceChange(id: string) {
    // Switching sources must not carry over the previous source's
    // in-progress drag -- each source has its own (possibly very
    // different) camera geometry. Reset to the default trapezoid; if the
    // user wants to see/tweak what's already assigned, the "reuse" button
    // below for that preset loads its exact points (see assignExisting).
    sourceId = id
    points = clonePoints(DEFAULT_QUAD_POINTS)
    const next = sources.find((s) => s.id === id)
    scrubMs = openAt(next)
    frameMs = openAt(next)
    name = ''
    status = null
    error = null
    offerDetect = false
  }

  // True after an assignment succeeds: the one moment "run detection again"
  // is the natural next step. The Phase-1 detect route exists precisely so
  // this success message stops sending people to a terminal (the old copy
  // read "re-run detect (CLI: splitstep detect)" -- a dead end for anyone
  // without the dev checkout).
  let offerDetect = $state(false)

  async function redetect() {
    if (!source || busy) return
    // Same cost warning the re-segment panel gives at click time, and
    // literally the same sentence -- ResegmentPanel's own stale-region
    // button shares this copy (see lib/resegment.ts). A re-detect rebuilds
    // from detector intervals, so starred/rejected carry over by overlap
    // but manual boundary edits and hand-made rallies do not survive it.
    const sure = window.confirm(redetectConfirmMessage())
    if (!sure) return
    busy = true
    error = null
    try {
      await api.detectSource(source.id)
      offerDetect = false
      status = 'Detection queued — the jobs badge above tracks it.'
    } catch (e) {
      error = e
    } finally {
      busy = false
    }
  }

  async function save() {
    if (!source || busy) return
    busy = true
    status = null
    error = null
    try {
      const created = await api.createPreset(
        name.trim() || defaultPresetName(sessionId, source.idx),
        points,
      )
      if (!created.id) throw new Error('createPreset did not return an id')
      await api.setPreset(source.id, created.id)
      // createPreset only returns {id}, not the full row -- refetch so
      // `presets` (and therefore "currently assigned" and the reuse list)
      // picks up the new preset's name instead of showing its raw id, and
      // so it's immediately reusable on another source without a reload.
      await refreshPresets()
      status =
        'Saved and assigned. Existing rallies were detected without this region — ' +
        'run detection again for it to take effect.'
      offerDetect = true
      onassigned()
    } catch (e) {
      error = e
    } finally {
      busy = false
    }
  }

  async function assignExisting(preset: Preset) {
    if (!source || busy) return
    busy = true
    status = null
    error = null
    // Load the preset's own shape into the editor too -- otherwise the
    // shaded region keeps showing whatever the trapezoid happened to be
    // (the default, or a leftover drag), which would misrepresent what was
    // actually just assigned to this source.
    points = clonePoints(preset.points)
    try {
      await api.setPreset(source.id, preset.id)
      status =
        `Assigned "${preset.name}". Existing rallies were detected without this ` +
        'region — run detection again for it to take effect.'
      offerDetect = true
      onassigned()
    } catch (e) {
      error = e
    } finally {
      busy = false
    }
  }
</script>

<details bind:open class="mt-6 rounded-lg border border-line">
  <summary class="cursor-pointer select-none p-4 text-body font-semibold">Play region</summary>

  <!-- `{#if open}` rather than letting <details> merely hide a mounted
       subtree: hidden children still load, and QuadCanvas' frame request
       makes the server extract a frame with ffmpeg. -->
  {#if open}
    <div class="px-4 pb-4">
      <p class="text-caption text-dim">
        Drag the four corners to cover the area both players move in, extended to the bottom of
        frame. Without this, adjacent public courts stay visible to detection and can be picked as
        the far player.
      </p>

      {#if sources.length > 1}
        <select
          value={sourceId}
          onchange={(e) => onSourceChange(e.currentTarget.value)}
          class="mt-3 rounded border border-line bg-surface px-2 py-1 text-body"
          aria-label="source to edit"
        >
          {#each sources as s (s.id)}
            <option value={s.id}>source {s.idx}</option>
          {/each}
        </select>
      {/if}

      {#if source}
        <p class="mt-2 font-data text-caption text-faint">
          currently assigned: {assignedLabel}
        </p>

        <QuadCanvas
          frameSrc={api.frameUrl(sessionId, source.idx, frameMs)}
          timeMs={scrubMs}
          maxMs={maxMs}
          fps={source.fps}
          points={points}
          onpoints={(p) => (points = p)}
          onseek={seek}
          alt="source {source.idx} at {formatTs(frameMs)}"
        />

        <div class="mt-3 flex items-center gap-2">
          <input
            bind:value={name}
            placeholder={defaultPresetName(sessionId, source.idx)}
            class="flex-1 rounded border border-line bg-surface px-2 py-1 text-body"
            aria-label="preset name"
          />
          <button
            class="rounded bg-fg px-3 py-1 text-body font-medium text-bg hover:bg-fg/90 disabled:opacity-40 motion-safe:transition-colors"
            onclick={save}
            disabled={busy}
          >
            {busy ? 'working…' : 'Save & assign'}
          </button>
        </div>

        {#if presets.length > 0}
          <div class="mt-3 flex flex-wrap gap-2">
            <span class="text-caption text-faint">reuse:</span>
            {#each presets as p (p.id)}
              <button
                class="rounded border border-line px-2 py-0.5 text-caption hover:bg-surface-2
                       disabled:opacity-40 motion-safe:transition-colors"
                onclick={() => assignExisting(p)}
                disabled={busy}
              >
                {p.name}
              </button>
            {/each}
          </div>
        {/if}

        <!-- After an assignment the status sentence and the action it
             implies are one thing, so they render as one card. They used to
             be a loose line plus a caption-sized text link, and the link was
             missed: the reviewer assigned a corrected region, reached for
             the re-segment slider instead, saw the same rallies come back
             and concluded re-segment was broken. Re-segment only replays
             cached features, which were built under the OLD region -- the
             one thing only a re-detect can fix, so it gets the app's filled
             primary treatment. -->
        {#if offerDetect}
          <div class="mt-3 rounded-lg border border-line bg-surface-2 p-3">
            {#if status}
              <p class="text-body text-fg">{status}</p>
            {/if}
            <button
              class="mt-3 rounded-lg bg-fg px-4 py-2 text-body font-semibold text-bg
                     hover:bg-fg/90 disabled:opacity-40 motion-safe:transition-colors"
              onclick={redetect}
              disabled={busy}
            >
              {busy ? 'working…' : 'Run detection with this region'}
            </button>
            <p class="mt-2 text-caption text-dim">
              Re-segment can't see a new region — detection rebuilds the features.
            </p>
          </div>
        {:else if status}
          <!-- The other status this panel shows -- "Detection queued" -- has
               no action left to offer, so it stays a plain line. -->
          <p class="mt-2 font-data text-caption text-fg">{status}</p>
        {/if}
        {#if error}
          <p class="mt-2 font-data text-data text-danger">{describeApiError(error, 'source').message}</p>
        {/if}
      {/if}
    </div>
  {/if}
</details>
