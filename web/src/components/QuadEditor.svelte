<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
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
  let error = $state<string | null>(null)
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
      error = String(e)
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
        'Saved and assigned. Existing rallies on this source were detected without this ' +
        'region -- re-run detect (CLI: splitstep detect) for it to take effect.'
      onassigned()
    } catch (e) {
      error = String(e)
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
        `Assigned "${preset.name}". Existing rallies on this source were detected without ` +
        'this region -- re-run detect (CLI: splitstep detect) for it to take effect.'
      onassigned()
    } catch (e) {
      error = String(e)
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
            class="rounded bg-accent px-3 py-1 text-body font-medium text-bg hover:brightness-110 disabled:opacity-40"
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
                       disabled:opacity-40"
                onclick={() => assignExisting(p)}
                disabled={busy}
              >
                {p.name}
              </button>
            {/each}
          </div>
        {/if}

        {#if status}
          <p class="mt-2 font-data text-caption text-accent">{status}</p>
        {/if}
        {#if error}
          <p class="mt-2 font-data text-data text-danger">{error}</p>
        {/if}
      {/if}
    </div>
  {/if}
</details>
