<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import {
    DEFAULT_QUAD_POINTS,
    assignedPresetLabel,
    clonePoints,
    defaultPresetName,
    movePoint,
    pointFromClient,
    polygonClipPath,
  } from '../lib/quad'
  import type { Preset, Source } from '../lib/types'

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

  let points = $state<[number, number][]>(clonePoints(DEFAULT_QUAD_POINTS))
  let name = $state('')
  let presets = $state<Preset[]>([])
  let dragging = $state<number | null>(null)
  let wrap = $state<HTMLDivElement>()
  let status = $state<string | null>(null)
  let error = $state<string | null>(null)
  // Guards both `save` and `assignExisting`: without it, a double-click on
  // "Save & assign" fires createPreset twice before the first request's
  // response lands, creating two identically-shaped presets under
  // (usually) the same name -- same double-submit class ResegmentPanel's
  // `busy` already guards against.
  let busy = $state(false)

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

  const polygon = $derived(polygonClipPath(points))
  const assignedLabel = $derived(assignedPresetLabel(source?.court_preset_id ?? null, presets))

  function onSourceChange(id: string) {
    // Switching sources must not carry over the previous source's
    // in-progress drag -- each source has its own (possibly very
    // different) camera geometry. Reset to the default trapezoid; if the
    // user wants to see/tweak what's already assigned, the "reuse" button
    // below for that preset loads its exact points (see assignExisting).
    sourceId = id
    points = clonePoints(DEFAULT_QUAD_POINTS)
    name = ''
    status = null
    error = null
  }

  function at(e: PointerEvent): [number, number] {
    if (!wrap) return [0, 0]
    return pointFromClient(e.clientX, e.clientY, wrap.getBoundingClientRect())
  }

  // Pointer capture and the move/up listeners live on the handle button
  // itself (a real interactive, focusable element), not on the wrapping
  // div -- so the drag keeps tracking the pointer even once it leaves the
  // button's own bounds, without needing pointer handlers on a static
  // `<div>` (which svelte-check's a11y check correctly flags: a plain div
  // with pointer handlers and no interactive role is not something a
  // keyboard or screen-reader user can operate). `at()` still measures
  // against `wrap`'s rect regardless of which element the event fired on,
  // since PointerEvent.clientX/Y are viewport-relative.
  function onHandleDown(e: PointerEvent, i: number) {
    dragging = i
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  }

  function onHandleMove(e: PointerEvent, i: number) {
    if (dragging !== i) return
    points = movePoint(points, i, at(e))
  }

  function endDrag() {
    dragging = null
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
        'region -- re-run detect (CLI: bootleg detect) for it to take effect.'
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
        'this region -- re-run detect (CLI: bootleg detect) for it to take effect.'
      onassigned()
    } catch (e) {
      error = String(e)
    } finally {
      busy = false
    }
  }
</script>

<section class="mt-6 rounded-lg border border-neutral-800 p-4">
  <h2 class="text-sm font-semibold">Play region</h2>
  <p class="mt-1 text-xs text-neutral-400">
    Drag the four corners to cover the area both players move in, extended to the bottom of
    frame. Without this, adjacent public courts stay visible to detection and can be picked as
    the far player.
  </p>

  {#if sources.length > 1}
    <select
      value={sourceId}
      onchange={(e) => onSourceChange(e.currentTarget.value)}
      class="mt-3 rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm"
      aria-label="source to edit"
    >
      {#each sources as s (s.id)}
        <option value={s.id}>source {s.idx}</option>
      {/each}
    </select>
  {/if}

  {#if source}
    <p class="mt-2 font-mono text-[11px] text-neutral-500">
      currently assigned: {assignedLabel}
    </p>

    <!--
      No fixed aspect ratio (e.g. Tailwind's `aspect-video`) and no
      `object-fit` on the image: the wrapper has no height of its own, so
      it sizes to exactly the image's rendered box (block img,
      `width:100%; height:auto`). That means this wrapper's own
      getBoundingClientRect() -- what `at()` and every handle's percentage
      position are computed against -- always equals the frame image's
      actual displayed rect, whatever the source video's aspect ratio is.
      An `object-contain` image inside a fixed-aspect box would letterbox
      when the two ratios differ, silently offsetting every corner from
      where the pointer actually is.
    -->
    <div bind:this={wrap} class="relative mt-3 w-full min-h-48 select-none bg-black">
      <!--
        No `overflow-hidden` here, deliberately: the default trapezoid (and
        any preset) puts two corner handles at y=1.0 -- the bottom edge of
        `wrap`, on purpose (see the module comment on DEFAULT_QUAD_POINTS).
        A handle centered exactly on that edge has half its hit area below
        it; `overflow-hidden` on this element would clip that half away,
        found live: a real pointer down dead-center on such a handle hit
        the `<section>` behind it, not the button, because the clip
        boundary sat right at the handle's own center. Rounding lives on
        the image itself instead, which needs no clipping parent to look
        rounded.
      -->
      <img
        src={api.frameUrl(sessionId, source.idx)}
        alt="first frame of source {source.idx}"
        class="block w-full h-auto rounded"
        draggable="false"
      />
      <div
        class="pointer-events-none absolute inset-0 bg-blue-400/20"
        style={`clip-path: ${polygon}`}
      ></div>
      {#each points as p, i (i)}
        <button
          class="absolute h-5 w-5 -translate-x-1/2 -translate-y-1/2 cursor-grab rounded-full
                 border-2 border-white bg-blue-500 touch-none"
          style={`left:${p[0] * 100}%;top:${p[1] * 100}%`}
          onpointerdown={(e) => onHandleDown(e, i)}
          onpointermove={(e) => onHandleMove(e, i)}
          onpointerup={endDrag}
          onpointercancel={endDrag}
          aria-label={`corner ${i + 1}`}
        ></button>
      {/each}
    </div>

    <div class="mt-3 flex items-center gap-2">
      <input
        bind:value={name}
        placeholder={defaultPresetName(sessionId, source.idx)}
        class="flex-1 rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm"
        aria-label="preset name"
      />
      <button
        class="rounded bg-blue-600 px-3 py-1 text-sm disabled:opacity-40"
        onclick={save}
        disabled={busy}
      >
        {busy ? 'working…' : 'Save & assign'}
      </button>
    </div>

    {#if presets.length > 0}
      <div class="mt-3 flex flex-wrap gap-2">
        <span class="text-xs text-neutral-500">reuse:</span>
        {#each presets as p (p.id)}
          <button
            class="rounded border border-neutral-700 px-2 py-0.5 text-xs hover:bg-neutral-800
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
      <p class="mt-2 font-mono text-xs text-amber-300">{status}</p>
    {/if}
    {#if error}
      <p class="mt-2 font-mono text-xs text-red-300">{error}</p>
    {/if}
  {/if}
</section>
