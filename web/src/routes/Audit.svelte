<script lang="ts">
  import { api } from '../lib/api'
  import { AuditController, spanLabelApi } from '../lib/audit'
  import { describeApiError } from '../lib/errors'
  import { isEditableTarget } from '../lib/keyboard'
  import { LabelWriter } from '../lib/labels'
  import { navigate } from '../lib/router.svelte'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import { formatTs } from '../lib/time'
  import type { LabelAction, Verdict } from '../lib/labels'
  import type { SampleWindow, Source } from '../lib/types'
  import KeyHints from '../components/KeyHints.svelte'
  import Mark from '../components/Mark.svelte'
  import VideoDeck from '../components/VideoDeck.svelte'

  interface Props {
    id: string
  }
  let { id }: Props = $props()

  // Same four as label mode, and deliberately the same digits: the two modes
  // ask one question about a span, and a reviewer crossing between them must
  // not have to remember which `2` means what.
  const VERDICT_KEYS: Record<string, Verdict> = {
    '1': 'clean',
    '2': 'not_play',
    '3': 'partly',
    '4': 'unsure',
  }
  const VERDICT_LABELS: Record<Verdict, string> = {
    clean: 'clean rally (1)',
    not_play: 'not play (2)',
    partly: 'partly play (3)',
    unsure: 'unsure (4)',
  }

  const SAMPLE_SIZE = 20

  const toaster = createToaster()
  let source = $state<Source | null>(null)
  let controller = $state<AuditController | null>(null)
  let writer = $state<LabelWriter | null>(null)
  let error = $state<unknown>(null)
  let windowMs = $state(8000)
  // The sample is recomputed from this, never stored -- so a reload on the
  // default seed returns the same twenty windows and the pass resumes where
  // it was, with the judgements (which ARE stored) seeded back onto them.
  let seed = $state(0)
  let version = $state(0)
  let deck = $state<VideoDeck>()

  // Keyed on `id` and `seed`, and both are read reactively on purpose: this
  // route is rendered unkeyed like Session and Setup, so navigating to
  // another source swaps the prop on the live instance. Clearing state up
  // front and guarding the response with `cancelled` is the same shape
  // tests/route-effect-staleness.test.ts pins for those two -- here a stale
  // response would show one source's windows over another's footage, and
  // every verdict typed against them would be a lie in the corpus.
  $effect(() => {
    const sourceId = id
    const drawSeed = seed
    let cancelled = false
    error = null
    source = null
    controller = null
    writer = null

    Promise.all([
      api.getSource(sourceId),
      api.labelSample(sourceId, SAMPLE_SIZE, drawSeed),
      api.sourceLabels(sourceId),
    ])
      .then(([src, sample, labels]) => {
        if (cancelled) return
        source = src
        windowMs = sample.window_ms
        controller = new AuditController(sample.windows, labels)
        writer = new LabelWriter(spanLabelApi(sourceId, api))
        version += 1
      })
      .catch((e) => {
        if (cancelled) return
        error = e
      })

    return () => {
      cancelled = true
    }
  })

  const current = $derived.by((): SampleWindow | null => {
    version
    return controller?.current ?? null
  })
  const verdict = $derived.by(() => {
    version
    return controller?.verdict ?? null
  })
  const stats = $derived.by(() => {
    version
    return {
      index: controller?.index ?? 0,
      total: controller?.total ?? 0,
      judged: controller?.judged ?? 0,
      done: controller?.done ?? false,
    }
  })

  const src = $derived(source ? api.proxyUrl(source.session_id, source.idx) : '')

  async function apply(action: LabelAction | null): Promise<void> {
    version += 1
    if (!action || !writer) return
    const outcome = await writer.submit(action)
    if (outcome.status === 'failed') {
      // Same handling as label mode: put the span back to what the server
      // last accepted, say so, and let the pass continue. A rare failure on
      // a LAN box is not a reason to stop a twenty-window sitting.
      controller?.restore(action.rallyId, outcome.restore)
      version += 1
      toaster.push("Couldn't save that judgement -- reverted")
    }
  }

  function onKey(e: KeyboardEvent) {
    if (isEditableTarget(e.target)) return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    if (!controller) return

    const v = VERDICT_KEYS[e.key]
    if (v) {
      apply(controller.setVerdict(v))
      return
    }
    switch (e.key) {
      case 'r':
      case 'R':
        deck?.replay()
        break
      case 'ArrowRight':
        e.preventDefault()
        controller.next()
        version += 1
        break
      case 'ArrowLeft':
        e.preventDefault()
        controller.prev()
        version += 1
        break
      case 'u':
      case 'U':
        apply(controller.undo())
        break
      case ' ':
        e.preventDefault()
        if (deck?.paused()) deck?.play()
        else deck?.pause()
        break
      case 'Escape':
        leave()
        break
    }
  }

  function leave() {
    if (source) navigate(`/s/${source.session_id}`)
    else navigate('/')
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="flex items-baseline justify-between">
  <h1 class="text-title font-semibold">Blind pass</h1>
  <button class="text-caption text-dim hover:text-fg" onclick={leave}>back to session (Esc)</button>
</div>

<!--
  Says what this is for, every time. A reviewer who thinks they are looking
  at detector output will judge the boundaries rather than the footage, and
  the windows here have no detector boundaries to judge.
-->
<p class="mt-2 max-w-3xl text-caption text-dim">
  Some of these windows the detector flagged and some it ignored, and nothing here says
  which. That is what makes recall measurable: judge what the footage shows, not what you
  think was proposed. {Math.round(windowMs / 1000)} s each.
</p>

{#if error}
  <p class="mt-6 font-data text-data text-danger">{describeApiError(error, 'source').message}</p>
{:else if !controller || !source}
  <div class="flex justify-center py-12">
    <Mark size={40} state="loading" label="Drawing a sample…" />
  </div>
{:else if !current}
  <section class="mt-6 rounded-lg border border-line p-8 text-center">
    <h2 class="text-title font-semibold">Nothing to sample</h2>
    <p class="mt-2 font-data text-body text-dim">
      This source is shorter than one window.
    </p>
  </section>
{:else}
  <div class="relative mt-4">
    <!--
      Loops inside the window rather than advancing, exactly as label mode
      does: letting the video run on means judging the next window's footage
      by accident. Speed stays at 1 -- a reach and a swing are not reliably
      distinguishable at 2x, and a wrong label is worse than a slow pass.
    -->
    <VideoDeck
      bind:this={deck}
      {src}
      startMs={current.start_ms}
      endMs={current.end_ms}
      onended={() => deck?.replay()}
    />
    <div
      class="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-1
             font-data text-data tabular-nums text-fg"
    >
      {stats.index + 1} / {stats.total} · {stats.judged} judged
    </div>
  </div>

  <div class="mt-3 flex flex-wrap items-center gap-2">
    {#each Object.entries(VERDICT_KEYS) as [key, v] (key)}
      <button
        class="rounded-lg border px-3 py-1.5 text-body motion-safe:transition-colors
               {verdict === v ? 'border-fg bg-fg text-bg' : 'border-line hover:bg-surface-2'}"
        onclick={() => apply(controller?.setVerdict(v) ?? null)}
      >
        {VERDICT_LABELS[v]}
      </button>
    {/each}
    <span class="font-data text-caption text-faint">
      {formatTs(current.start_ms)} – {formatTs(current.end_ms)}
    </span>
  </div>

  {#if stats.done}
    <!--
      The sample is exhausted, not the question. A second pass on a fresh
      seed is what the 2026-08-21 set's own caveat asks for -- fifteen
      windows was small, and anything separating cleanly on one pass wants
      re-checking against another.
    -->
    <div class="mt-4 rounded-lg border border-line bg-surface-2 p-3">
      <p class="text-body text-fg">
        All {stats.total} windows judged. Score it with
        <code class="font-data">splitstep labels score {id}</code>.
      </p>
      <button
        class="mt-3 rounded-lg bg-fg px-4 py-2 text-body font-semibold text-bg
               hover:bg-fg/90 motion-safe:transition-colors"
        onclick={() => (seed += 1)}
      >
        Draw another sample
      </button>
    </div>
  {/if}

  <KeyHints mode="audit" />
{/if}

{#each toaster.toasts as toast (toast.id)}
  <div class="fixed bottom-6 left-1/2 -translate-x-1/2 rounded px-3 py-2 text-body
              {toastToneClasses(toast.tone)}">
    {toast.message}
  </div>
{/each}
