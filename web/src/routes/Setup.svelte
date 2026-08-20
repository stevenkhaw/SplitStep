<script lang="ts">
  import QuadCanvas from '../components/QuadCanvas.svelte'
  import { api } from '../lib/api'
  import { previewTimestamps } from '../lib/preview'
  import { DEFAULT_QUAD_POINTS, clonePoints, defaultPresetName } from '../lib/quad'
  import { clamp, formatTs, lastSafeFrameMs } from '../lib/time'
  import type { Preset, Source } from '../lib/types'

  let { id }: { id: string } = $props()

  let source = $state<Source | null>(null)
  let presets = $state<Preset[]>([])
  let rotation = $state(0)
  let workingMs = $state(0)
  let scrubMs = $state(0)
  let points = $state<[number, number][] | null>(null) // null until the user commits one
  // Which existing preset (if any) `points` was last loaded from verbatim.
  // Confirm reuses this id instead of always calling createPreset -- every
  // confirm previously created a new "<session> source <idx>" row, with no
  // dedupe and no unique-name constraint to stop it, even when the user had
  // just clicked an existing preset button that only copies its
  // coordinates. Cleared whenever `points` changes to something that did
  // NOT come from a preset (the default trapezoid, or a dragged corner) so
  // a subsequent Confirm can't silently reuse a preset the user has since
  // diverged from.
  let selectedPresetId = $state<string | null>(null)
  let busy = $state(false)
  let error = $state<string | null>(null)

  const timestamps = $derived(source ? previewTimestamps(source.duration_ms, source.fps) : [])
  const maxMs = $derived(source ? lastSafeFrameMs(source.duration_ms, source.fps) : 0)

  $effect(() => {
    api
      .getSource(id)
      .then((s) => {
        source = s
        rotation = s.rotation_deg
        // The working frame starts at the first grid timestamp rather than
        // t=0 for the same reason the grid skips it: phone footage opens on
        // a black frame more often than not.
        workingMs = previewTimestamps(s.duration_ms, s.fps)[0] ?? 0
        scrubMs = workingMs
        return api.listPresets()
      })
      .then((p) => {
        presets = p
      })
      .catch((e) => {
        error = String(e)
      })
  })

  function rotate(dir: 1 | -1) {
    rotation = (rotation + dir * 90 + 360) % 360
  }

  function seek(ms: number) {
    const next = Math.round(clamp(ms, 0, maxMs))
    workingMs = next
    scrubMs = next
  }

  // `presetId` is set when `next` is an existing preset's own points
  // (reused verbatim), null for the default trapezoid.
  function usePoints(next: [number, number][], presetId: string | null = null) {
    points = clonePoints(next)
    selectedPresetId = presetId
  }

  async function start() {
    if (!source || !points || busy) return
    busy = true
    error = null
    try {
      // Reuse the preset the current points came from verbatim; only
      // create a new row when the user never picked one, or picked one and
      // then dragged a corner (usePoints/onpoints both clear
      // selectedPresetId the moment `points` stops matching an existing
      // preset's coordinates).
      let presetId = selectedPresetId
      if (!presetId) {
        const created = await api.createPreset(
          defaultPresetName(source.session_id, source.idx),
          points,
        )
        if (!created.id) throw new Error('createPreset did not return an id')
        presetId = created.id
        // Assigned immediately -- before the api.setup() call below, which
        // can still fail. Leaving this only in the local `presetId` meant a
        // failed setup() left selectedPresetId at null, so every retry hit
        // the `!presetId` branch again and inserted another orphaned preset
        // row indistinguishable from the last. Setting it here means a
        // retry reuses the row this attempt already created.
        selectedPresetId = presetId
      }
      await api.setup(source.id, rotation, presetId)
      window.location.hash = `/s/${source.session_id}`
    } catch (e) {
      error = String(e)
    } finally {
      busy = false
    }
  }
</script>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{/if}

{#if !source}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else}
  <header class="mb-4">
    <h1 class="text-xl font-semibold">Set up source {source.idx}</h1>
    <p class="mt-1 text-sm text-neutral-400">
      Pick the rotation and drag the play region over a real frame, then start detection.
    </p>
    {#if source.status === 'ready'}
      <p class="mt-2 rounded bg-amber-500/10 p-3 text-xs text-amber-300">
        Re-running setup rebuilds the proxy and replaces this source's rallies, including any
        boundaries you hand-edited.
      </p>
    {/if}
  </header>

  <section>
    <div class="flex items-center gap-3">
      <button
        class="rounded border border-neutral-700 px-2 py-1 text-sm hover:bg-neutral-800"
        onclick={() => rotate(-1)}
        aria-label="rotate counter-clockwise"
      >
        &#8634;
      </button>
      <span class="font-mono text-xs text-neutral-400">{rotation}&deg;</span>
      <button
        class="rounded border border-neutral-700 px-2 py-1 text-sm hover:bg-neutral-800"
        onclick={() => rotate(1)}
        aria-label="rotate clockwise"
      >
        &#8635;
      </button>
    </div>

    <!--
      Nine tiles at a real, readable size -- not thumbnails -- because the
      whole point of this grid is to make a wrong rotation obvious at a
      glance. Rotation applies to every tile at once: a clip whose metadata
      says 90 but is actually upside down looks *wrong* here immediately,
      the way it never would staring at one frame at a time.
    -->
    <div class="mt-3 grid grid-cols-3 gap-2">
      {#each timestamps as t (t)}
        <button
          class="overflow-hidden rounded border border-neutral-800 hover:border-neutral-600"
          onclick={() => seek(t)}
        >
          <img
            data-grid
            src={api.previewUrl(source.session_id, source.idx, t, rotation)}
            alt="frame at {formatTs(t)}"
            class="block h-auto w-full"
          />
        </button>
      {/each}
    </div>
  </section>

  <section class="mt-6">
    <h2 class="text-sm font-semibold">Play region</h2>
    <p class="mt-1 text-xs text-neutral-400">
      Drag the four corners to cover the area both players move in, extended to the bottom of
      frame.
    </p>

    <div class="mt-3 flex flex-wrap items-center gap-2">
      <button
        class="rounded border border-neutral-700 px-2 py-0.5 text-xs hover:bg-neutral-800"
        onclick={() => usePoints(DEFAULT_QUAD_POINTS)}
        aria-label="use default play region"
      >
        use default play region
      </button>
      {#each presets as p (p.id)}
        <button
          class="rounded border border-neutral-700 px-2 py-0.5 text-xs hover:bg-neutral-800"
          onclick={() => usePoints(p.points, p.id)}
          aria-label="use preset {p.name}"
        >
          {p.name}
        </button>
      {/each}
    </div>

    <QuadCanvas
      frameSrc={api.previewUrl(source.session_id, source.idx, workingMs, rotation)}
      timeMs={scrubMs}
      {maxMs}
      fps={source.fps}
      points={points ?? DEFAULT_QUAD_POINTS}
      onpoints={(p) => {
        points = p
        // A dragged corner no longer matches the preset it may have
        // started from -- Confirm must create a new preset, not silently
        // overwrite the one the user reused.
        selectedPresetId = null
      }}
      onseek={seek}
      alt="source {source.idx} at {formatTs(workingMs)}"
      frameErrorHint="the original may still be processing"
    />
  </section>

  <button
    class="mt-6 rounded bg-blue-600 px-4 py-1.5 text-sm disabled:opacity-40"
    onclick={start}
    disabled={!points || busy}
    aria-label="start detection"
  >
    {busy ? 'working…' : 'start detection'}
  </button>
{/if}
