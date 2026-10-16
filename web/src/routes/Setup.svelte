<script lang="ts">
  import ErrorNote from '../components/ErrorNote.svelte'
  import Mark from '../components/Mark.svelte'
  import MountFormatPicker from '../components/MountFormatPicker.svelte'
  import QuadCanvas from '../components/QuadCanvas.svelte'
  import { api } from '../lib/api'
  import { CORNER_LABELS, DEFAULT_CORNER_POINTS, needsCorners, parseCorners } from '../lib/mount'
  import { previewTimestamps } from '../lib/preview'
  import { DEFAULT_QUAD_POINTS, clampQuad, clonePoints, defaultPresetName } from '../lib/quad'
  import { navigate } from '../lib/router.svelte'
  import { clamp, formatTs, lastSafeFrameMs } from '../lib/time'
  import type { Mount, PlayFormat, Preset, Source } from '../lib/types'

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
  // The two questions that pick a detector, answered here beside rotation.
  let mount = $state<Mount>('')
  let format = $state<PlayFormat>('')
  // Court corners, kept apart from `points` so flipping the mount back and
  // forth loses neither drawing. Null until a handle moves or the source
  // already has corners stored: the default shape is a starting point, and
  // a court nobody placed must not be sendable.
  let corners = $state<[number, number][] | null>(null)
  let busy = $state(false)
  let error = $state<unknown>(null)

  const timestamps = $derived(source ? previewTimestamps(source.duration_ms, source.fps) : [])
  const maxMs = $derived(source ? lastSafeFrameMs(source.duration_ms, source.fps) : 0)
  // A fence mount is drawn as court corners and the server derives the play
  // region from them -- one drawing, so the two cannot disagree.
  const cornersMode = $derived(needsCorners(mount, format))

  $effect(() => {
    let cancelled = false
    // Both cleared at the start of each attempt. This effect re-runs whenever
    // `id` changes, and App.svelte renders `<Setup id={router.current.id} />`
    // unkeyed, so navigating from one source's wizard to another swaps the
    // prop on this live instance rather than remounting -- leaving the
    // previous source on screen under the new id, and the previous error over
    // a source that loaded fine.
    error = null
    source = null
    // The quad goes too, even though it is the user's own work rather than
    // anything this effect fetched -- which is exactly why it was missed.
    // Carrying it meant landing on the next source's wizard with the previous
    // source's play region already committed and `start detection` enabled:
    // one click from queueing a rebuild and a detect against a region drawn
    // over different footage.
    points = null
    selectedPresetId = null
    // And the answers, for the same reason as the quad: carried over, the
    // previous source's Fence + Singles and its court corners would be one
    // click from a fence setup against footage nobody drew them on.
    mount = ''
    format = ''
    corners = null

    // Defaults alongside the source, not after: the selectors render with
    // the source, and a default landing after a click would overwrite the
    // reviewer's own answer. A failed defaults call costs only the
    // pre-selection, never the wizard.
    Promise.all([api.getSource(id), api.setupDefaults().catch(() => null)])
      .then(([s, defaults]) => {
        if (cancelled) return null
        source = s
        // The source's own answers first (a re-run of setup), then the
        // library's most recent ones -- normally Fence + Singles, so
        // Steven only confirms.
        mount = s.mount || defaults?.mount || ''
        format = s.play_format || defaults?.play_format || ''
        corners = parseCorners(s.court_corners)
        rotation = s.rotation_deg
        // The working frame starts at the first grid timestamp rather than
        // t=0 for the same reason the grid skips it: phone footage opens on
        // a black frame more often than not.
        workingMs = previewTimestamps(s.duration_ms, s.fps)[0] ?? 0
        scrubMs = workingMs
        return api.listPresets()
      })
      .then((p) => {
        if (cancelled || p === null) return
        presets = p
      })
      .catch((e) => {
        if (!cancelled) error = e
      })

    // Stricter here than on the session page, because this wizard writes.
    // `start()` submits `api.setup(source.id, ...)` -- the id off the loaded
    // source, not the prop -- so a superseded response landing over the
    // current one means a quad dragged on screen queues a proxy rebuild and a
    // fifteen-minute detect against a different source entirely, then
    // navigates away as though it had worked.
    return () => {
      cancelled = true
    }
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
    if (!source || busy) return
    if (cornersMode ? !corners : !points) return
    busy = true
    error = null
    try {
      if (cornersMode && corners) {
        // Corners only: the server derives the play region from them
        // (region_from_corners) and writes that preset itself. Its points
        // leave the frame, which is why no client path creates it.
        await api.setup(source.id, rotation, null, {
          mount,
          play_format: format,
          court_corners: corners,
        })
        window.location.hash = `/s/${source.session_id}`
        return
      }
      if (!points) return
      // Reuse the preset the current points came from verbatim; only
      // create a new row when the user never picked one, or picked one and
      // then dragged a corner (usePoints/onpoints both clear
      // selectedPresetId the moment `points` stops matching an existing
      // preset's coordinates).
      let presetId = selectedPresetId
      if (!presetId) {
        // Clamped: a derived "Court from corners" preset can be loaded
        // here verbatim and carries off-frame points, which this route
        // refuses. QuadCanvas already commits a clamped quad on any drag;
        // this makes "never sent back" hold on every path, not most.
        const created = await api.createPreset(
          defaultPresetName(source.session_id, source.idx),
          clampQuad(points),
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
      await api.setup(source.id, rotation, presetId, { mount, play_format: format })
      window.location.hash = `/s/${source.session_id}`
    } catch (e) {
      error = e
    } finally {
      busy = false
    }
  }
</script>

{#if error}
  <ErrorNote {error} subject="source" />
{/if}

{#if !source}
  <div class="flex justify-center py-12">
    <Mark size={40} state="loading" />
  </div>
{:else}
  <header class="mb-4">
    <button class="font-data text-caption text-faint hover:text-dim" onclick={() => navigate('/')}>
      Sessions
    </button>
    <span class="font-data text-caption text-faint"> › </span>
    <h1 class="mt-1 text-display font-semibold">Set up source {source.idx}</h1>
    <!-- bg-surface, not bare: `dim` on browse's court-run scrim (the run-off
         apron most of the viewport's middle band shows) measures under
         4.5:1 -- CourtGround is viewport-fixed, so a scroll can carry any
         block of page content through that band regardless of how far down
         the page it sits. `fg`, just above, is the one token exempted for
         headings; this is body copy and needs the card. -->
    <p class="mt-1 inline-block rounded bg-surface px-2 py-1 text-body text-dim">
      Pick the rotation, say where the camera is and what is being played, and outline the
      court over a real frame, then start detection.
    </p>
    {#if source.status === 'ready'}
      <p class="mt-2 rounded bg-danger/10 p-3 text-caption text-danger">
        Re-running setup rebuilds the proxy and replaces this source's rallies, including any
        boundaries you hand-edited.
      </p>
    {/if}
  </header>

  <section>
    <!-- rounded border bg-surface, not a bare flex row: the rotation-degree
         readout between the two buttons is `dim` text with no surface
         under it otherwise -- this section has no heading and sits right
         on the court, so a scroll can carry it through browse's 0.18 scrim
         band the same way the Reel.svelte action bar was carried through
         it (see that fix's comment). Wrapping the whole row, not just the
         label, matches that fix and reads better than a lone chip floating
         between two bordered buttons. -->
    <div class="flex items-center gap-3 rounded-lg border border-line bg-surface p-3">
      <button
        class="rounded border border-line px-2 py-1 text-body hover:bg-surface-2 motion-safe:transition-colors"
        onclick={() => rotate(-1)}
        aria-label="rotate counter-clockwise"
      >
        &#8634;
      </button>
      <span class="font-data text-data text-dim">{rotation}&deg;</span>
      <button
        class="rounded border border-line px-2 py-1 text-body hover:bg-surface-2 motion-safe:transition-colors"
        onclick={() => rotate(1)}
        aria-label="rotate clockwise"
      >
        &#8635;
      </button>
    </div>

    <!-- The same surface card as rotation above, for the same reason: the
         row labels are `dim`, which may not cross bare court. -->
    <div class="mt-3 rounded-lg border border-line bg-surface p-3">
      <MountFormatPicker
        {mount}
        {format}
        onmount={(m) => (mount = m)}
        onformat={(f) => (format = f)}
        disabled={busy}
      />
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
          class="overflow-hidden rounded border border-line hover:border-line motion-safe:transition-colors"
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

  {#if cornersMode}
    <section class="mt-6">
      <h2 class="text-body font-semibold">Court corners</h2>
      <!-- The copy says which lines, because "the court" is ambiguous on a
           doubles court and the homography needs the doubles outline. -->
      <p class="mt-1 inline-block rounded bg-surface px-2 py-1 text-caption text-dim">
        Drag the four handles onto the court's corners — doubles sidelines and both baselines.
      </p>
      <QuadCanvas
        frameSrc={api.previewUrl(source.session_id, source.idx, workingMs, rotation)}
        timeMs={scrubMs}
        {maxMs}
        fps={source.fps}
        points={corners ?? DEFAULT_CORNER_POINTS}
        labels={CORNER_LABELS}
        onpoints={(p) => (corners = p)}
        onseek={seek}
        alt="source {source.idx} at {formatTs(workingMs)}"
        frameErrorHint="the original may still be processing"
      />
    </section>
  {:else}
    <section class="mt-6">
      <h2 class="text-body font-semibold">Play region</h2>
      <p class="mt-1 inline-block rounded bg-surface px-2 py-1 text-caption text-dim">
        Drag the four corners to cover the area both players move in, extended to the bottom of
        frame.
      </p>

      <div class="mt-3 flex flex-wrap items-center gap-2">
        <button
          class="rounded border border-line px-2 py-0.5 text-caption hover:bg-surface-2 motion-safe:transition-colors"
          onclick={() => usePoints(DEFAULT_QUAD_POINTS)}
          aria-label="use default play region"
        >
          use default play region
        </button>
        {#each presets as p (p.id)}
          <button
            class="rounded border border-line px-2 py-0.5 text-caption hover:bg-surface-2 motion-safe:transition-colors"
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
  {/if}

  <button
    class="mt-6 rounded bg-fg px-4 py-1.5 text-body font-medium text-bg hover:bg-fg/90 disabled:opacity-40 motion-safe:transition-colors"
    onclick={start}
    disabled={(cornersMode ? !corners : !points) || busy}
    aria-label="start detection"
  >
    {busy ? 'working…' : 'start detection'}
  </button>
  <!-- The why and the cost, stated at the moment of commitment (spec §Phase
       2): the quad's purpose was previously explained only inside
       QuadEditor's session-page copy, and the twenty-minute encode+detect
       surprised anyone who expected an upload-style progress bar. One
       bg-surface card for both lines, not bare text -- see the comment on
       the header subtitle above for why. -->
  <div class="mt-3 max-w-md rounded bg-surface p-3">
    <p class="text-caption text-dim">
      Outlining your court keeps players on the next court out of your rallies.
    </p>
    <p class="mt-1 text-caption text-faint">
      Detection takes roughly 20 minutes for an hour of 4K footage; you can keep
      using the app while it runs.
    </p>
  </div>
{/if}
