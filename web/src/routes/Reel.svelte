<script lang="ts">
  import AddRalliesPicker from '../components/AddRalliesPicker.svelte'
  import JobsBadge from '../components/JobsBadge.svelte'
  import ReelItemList from '../components/ReelItemList.svelte'
  import ReelPreview from '../components/ReelPreview.svelte'
  import { api } from '../lib/api'
  import { describeExportCounts } from '../lib/export'
  import { reelMembershipKey, renderBlockedReason, spanRef } from '../lib/reels'
  import { navigate } from '../lib/router.svelte'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import type { ReelDetail, ReelItem, SpanRef } from '../lib/types'

  let { slug }: { slug: string } = $props()

  let detail = $state<ReelDetail | null>(null)
  let error = $state<string | null>(null)
  let loading = $state(true)
  let showPicker = $state(false)
  let showPreview = $state(false)
  let busy = $state(false)
  const toaster = createToaster()

  // Bumped after every successful mutation to force a refetch. The server is
  // the source of truth for clip_ready and for orphan status -- both can
  // change under us (an encode finishing, a re-segment in another tab) --
  // so the list is re-read rather than patched locally.
  let revision = $state(0)

  $effect(() => {
    const currentSlug = slug
    revision
    let cancelled = false
    loading = true
    error = null
    api
      .getReel(currentSlug)
      .then((d) => {
        if (!cancelled) detail = d
      })
      .catch((e) => {
        if (!cancelled) error = String(e)
      })
      .finally(() => {
        if (!cancelled) loading = false
      })
    return () => {
      cancelled = true
    }
  })

  const items = $derived(detail?.items ?? [])
  const blocked = $derived(renderBlockedReason(items))
  const existing = $derived<SpanRef[]>(items.map(spanRef))

  async function mutate(fn: () => Promise<unknown>, failure: string): Promise<void> {
    // One in-flight mutation at a time -- membership/order writes AND the
    // export and render kickoffs all funnel through here now, so a fast
    // double-click on any one button can't fire two overlapping POSTs.
    // Every one of these rewrites server state and then refetches;
    // overlapping them would let an older response land after a newer one
    // and render a state the user has already moved past -- the same
    // hazard LabelWriter serialises against.
    //
    // The `if (busy) return` below is a backstop, not the primary guard:
    // every button that calls into `mutate` is also `disabled={busy}` in
    // the template, so a click ordinarily can't reach here while another
    // mutation is in flight -- the user sees a disabled button, not a click
    // that silently did nothing. This still checks `busy` itself, in case
    // an event slips in between the click and the DOM reflecting it (a
    // dispatched-not-clicked event, a stale reference to the element from
    // before a re-render); dropping it would trade one silent no-op for
    // another, just a rarer one.
    if (busy) return
    busy = true
    try {
      await fn()
      revision += 1
    } catch (e) {
      toaster.push(`${failure} -- ${String(e)}`)
      // Refetch anyway: a rejected reorder means our view of the membership
      // is stale, and leaving the stale list on screen is what makes the
      // next drag fail too.
      revision += 1
    } finally {
      busy = false
    }
  }

  function commitOrder(next: ReelItem[]): void {
    mutate(() => api.setReelOrder(slug, next.map(spanRef)), "Couldn't reorder")
  }

  function remove(item: ReelItem): void {
    mutate(() => api.removeReelItem(slug, spanRef(item)), "Couldn't remove that clip")
  }

  function addSpans(spans: SpanRef[]): void {
    showPicker = false
    mutate(async () => {
      const result = await api.addReelItems(slug, spans)
      toaster.push(
        `${result.added} added${result.existing ? `, ${result.existing} already in` : ''}`,
        'info',
      )
    }, "Couldn't add those rallies")
  }

  function cutMissing(): void {
    // Routed through `mutate` like every other action here: it used to
    // fire outside the one-in-flight guard, so a fast double-click could
    // send two overlapping export POSTs. That happened to be harmless only
    // because the server's plan_reel_export buckets a re-click into
    // `in_flight` rather than re-queueing -- a safety net standing in for a
    // guarantee this page's own code claims to make. The four-counts
    // reporting is unchanged: encode progress is still the jobs badge's
    // job, not a second progress UI here.
    mutate(async () => {
      const result = await api.exportReelClips(slug)
      toaster.push(`Clips: ${describeExportCounts(result)}`, 'info')
    }, "Couldn't cut clips")
  }

  function render(): void {
    // Guarded by `blocked` on the button too; repeated here because the
    // button is not the only way this can be reached once a clip is deleted
    // between the fetch and the click.
    if (blocked) return
    // Same reasoning as cutMissing: routed through `mutate` so a double
    // click can't fire two render requests, even though enqueue_reel_once's
    // locked check-and-insert already makes a second one harmless server
    // side.
    mutate(async () => {
      const result = await api.renderReel(slug)
      toaster.push(
        result.already_running ? 'Already rendering.' : 'Rendering — see the jobs badge.',
        'info',
      )
    }, "Couldn't render")
  }
</script>

<header class="mb-6 flex items-baseline justify-between">
  <div>
    <button class="font-mono text-xs text-neutral-400 hover:text-neutral-200"
            onclick={() => navigate('/reels')}>← Reels</button>
    <h1 class="mt-1 text-xl font-semibold">{detail?.reel.name ?? slug}</h1>
  </div>
  <JobsBadge />
</header>

{#if error}
  <p class="rounded bg-red-500/10 p-3 text-sm text-red-300">{error}</p>
{:else if loading && !detail}
  <p class="text-sm text-neutral-400">Loading…</p>
{:else if detail}
  <div class="mb-4 flex flex-wrap items-center gap-3">
    <button
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800"
      onclick={() => (showPicker = !showPicker)}
    >Add rallies</button>

    <button
      data-preview
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={items.length === 0}
      onclick={() => (showPreview = !showPreview)}
    >{showPreview ? 'Hide preview' : 'Preview'}</button>

    <!-- Cutting is the ONLY action here that starts an encode. Render never
         enqueues clips: a button labelled "render" must not silently launch
         half an hour of work. Both are also disabled while `busy`: they now
         go through the same one-in-flight `mutate` as every other action
         here, and disabling is how a click while busy avoids being a
         silent no-op -- the user sees why nothing happened instead of
         wondering whether the click registered. -->
    <button
      data-cut
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={items.length === 0 || busy}
      onclick={cutMissing}
    >{busy ? 'Working…' : 'Cut missing clips'}</button>

    <button
      data-render
      class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs text-neutral-200
             hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={blocked !== null || busy}
      title={blocked ?? ''}
      onclick={render}
    >{busy ? 'Working…' : blocked ? `Render — ${blocked}` : 'Render'}</button>

    <span class="ml-auto font-mono text-xs text-neutral-500">
      {items.length} clips{detail.reel.rendered_path && !detail.reel.dirty
        ? ` · ${detail.reel.rendered_path}`
        : ''}
    </span>
  </div>

  {#if showPicker}
    <div class="mb-4">
      <AddRalliesPicker
        {existing}
        defaultSessionId={items.at(-1)?.session_id ?? null}
        onadd={addSpans}
        onclose={() => (showPicker = false)}
      />
    </div>
  {/if}

  {#if showPreview && items.length > 0}
    <!-- Keyed on reelMembershipKey(items), NOT on `items` itself: `items`
         comes back from a refetch as a fresh array of fresh objects every
         time (JSON never shares identity with what produced it), so keying
         on the reference remounted the preview after every successful
         mutation -- and after a failed one's recovery refetch -- even
         though none of those change which spans are in the reel or their
         order. reelMembershipKey only changes when membership or order
         actually does, so the preview remounts with a fresh controller
         exactly when one is needed (the same guarantee Session.svelte gives
         QueueMode) and otherwise keeps playing through an unrelated
         refetch. -->
    <div class="mb-4">
      {#key reelMembershipKey(items)}
        <ReelPreview {items} onclose={() => (showPreview = false)} />
      {/key}
    </div>
  {/if}

  {#if items.length === 0}
    <p class="text-sm text-neutral-400">
      Nothing in this reel yet. Add rallies above, or compile a session's points from the
      end of its review queue.
    </p>
  {:else}
    <ReelItemList {items} oncommit={commitOrder} onremove={remove} />
  {/if}
{/if}

{#if toaster.toasts.length > 0}
  <div class="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col
              items-center gap-2">
    {#each toaster.toasts as t (t.id)}
      <p class="rounded-full px-4 py-2 font-mono text-xs {toastToneClasses(t.tone)}">
        {t.message}
      </p>
    {/each}
  </div>
{/if}
