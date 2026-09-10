<script lang="ts">
  import { untrack } from 'svelte'
  import ErrorNote from '../components/ErrorNote.svelte'
  import AddRalliesPicker from '../components/AddRalliesPicker.svelte'
  import Mark from '../components/Mark.svelte'
  import ReelItemList from '../components/ReelItemList.svelte'
  import ReelPreview from '../components/ReelPreview.svelte'
  import { api } from '../lib/api'
  import { describeExportCounts } from '../lib/export'
  import { startPolling } from '../lib/polling'
  import {
    REEL_POLL_INTERVAL_MS,
    canWatchRendered,
    deleteConfirmationText,
    normalizedReelName,
    reelMembershipKey,
    renderBlockedReason,
    shouldPollReel,
    spanRef,
  } from '../lib/reels'
  import { navigate } from '../lib/router.svelte'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import type { ReelDetail, ReelItem, SpanRef } from '../lib/types'

  let { slug }: { slug: string } = $props()

  let detail = $state<ReelDetail | null>(null)
  let error = $state<unknown>(null)
  let loading = $state(true)
  let showPicker = $state(false)
  let showPreview = $state(false)
  let showWatch = $state(false)
  let busy = $state(false)
  let editingName = $state(false)
  let nameBuffer = $state('')
  // On by default at Steven's request (2026-08-26): the numbered overlay is
  // the reason he renders at all, so the default follows the common case.
  // The cost asymmetry still matters -- numbered re-encodes every clip
  // (~4-8x footage duration, see the reel job's numbered branch) where plain
  // is the fast -c copy path -- which is why this stays a visible checkbox
  // rather than becoming the only mode: unticking it is the escape hatch
  // for a quick plain render.
  let numbered = $state(true)
  // Off by default: it depends on RallyMetrics having rendered every clip,
  // and the server says exactly which are missing when it has not.
  let hr = $state(false)
  let hrRoot = $state<string | null>(null)
  let hrAvailable = $state(false)
  $effect(() => {
    void api.config().then((c) => {
      hrRoot = c.hr_clips_root
      hrAvailable = c.hr_clips_available
    }).catch(() => {})
  })
  // Two-step, inline: the first press only reveals what pressing it again
  // destroys (deleteConfirmationText below, rendered where confirmingDelete
  // gates the markup), never a browser confirm() -- a native dialog cannot
  // show that formatted a reel name plus a byte count, and its default
  // button can be fired by a stray Enter the user didn't mean for this.
  // No reset wired to the fetch/revision effect below: a successful delete
  // navigates away before it would matter, and a failed one leaves the
  // reel as it was -- re-showing the confirmation on refetch would be
  // wrong regardless, and confirmDelete already collapses this back to
  // false as soon as the confirming click fires, before the mutation
  // itself even starts.
  let confirmingDelete = $state(false)
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
        if (!cancelled) error = e
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
  const canWatch = $derived(detail ? canWatchRendered(detail.reel) : false)

  // Whether there is still something worth polling for. Read through a
  // `$derived` (not `shouldPollReel(items)` inlined in the effect below) so
  // the effect only reruns on a genuine true<->false flip: `detail` gets a
  // fresh `items` array on every fetch, poll-driven or not, and reacting to
  // that directly would tear the poller down and rebuild it on every tick.
  const hasMissingClips = $derived(shouldPollReel(items))

  $effect(() => {
    // The whole-branch finding this fixes: cutMissing() used to leave
    // Render's "N clips not cut yet" caption stale for the entire time the
    // jobs badge counted the encodes down, because nothing on this page
    // re-read the reel once cutting kicked off. Polling only while a clip
    // is missing, and stopping the moment none are, is what keeps that from
    // becoming a page that polls a fully-rendered reel forever -- a request
    // every few seconds against a server that is single-threaded for jobs
    // and shares its worker pool with media serving, for a reel that will
    // never change again on its own.
    if (!hasMissingClips) return
    // Snapshotted like the fetch effect above, and for the same reason:
    // reading the `slug` prop inside the async fetcher would make it a
    // tracked dependency of this effect too (its first tick runs
    // synchronously, before any `await`), restarting the poll loop on any
    // prop tick rather than only on a missing-clips transition.
    const currentSlug = slug
    const poller = startPolling(async () => {
      // Read untracked for the same reason `currentSlug` is snapshotted
      // above: this closure's first invocation runs synchronously inside
      // this effect, so a tracked read of `busy` here would resubscribe
      // the effect to every mutation start/end and rebuild the whole
      // poller each time -- the "fighting an in-flight mutation" this
      // guard exists to prevent, not cause. Skipping (rather than queuing
      // to run the instant `busy` clears) is enough: `mutate()`
      // unconditionally bumps `revision` when it finishes, success or
      // failure, and the fetch effect above refetches on that regardless
      // -- so a skipped tick costs at most one poll interval of staleness,
      // never a stuck page.
      if (untrack(() => busy)) return
      try {
        // Assigned straight to `detail`, not routed through `revision`:
        // the revision effect sets `loading = true` on every run, and a
        // silent background poll must not flash the whole page to
        // "Loading…" out from under someone just reading the list.
        //
        // Nothing else needs to guard against this refetch. The preview is
        // keyed on `reelMembershipKey`, not on `items` identity (see that
        // function's comment), so a poll that leaves membership and order
        // unchanged does not remount it or interrupt playback; and
        // `ReelItemList` only reads its `items` prop when no drag is in
        // progress (`shown` falls back to its own local `order` mid-drag),
        // so a poll landing mid-drag cannot stomp that either.
        detail = await api.getReel(currentSlug)
      } catch {
        // A background refresh failing is not worth a toast for a page
        // nobody may currently be watching -- the next tick just retries.
      }
    }, REEL_POLL_INTERVAL_MS)
    return () => poller.stop()
  })

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
      const result = await api.renderReel(slug, numbered, hr)
      toaster.push(
        result.already_running ? 'Already rendering.' : 'Rendering — see the jobs badge.',
        'info',
      )
    }, "Couldn't render")
  }

  function setItemNote(item: ReelItem, note: string): void {
    mutate(() => api.setReelItemNote(slug, spanRef(item), note), "Couldn't save that note")
  }

  function startRename(): void {
    if (!detail) return
    nameBuffer = detail.reel.name
    editingName = true
  }

  function cancelRename(): void {
    editingName = false
  }

  function saveName(): void {
    const name = normalizedReelName(nameBuffer)
    if (name === null) return
    // Closed immediately, like commitNote in QueueMode.svelte -- the field
    // is gone before the round trip starts, not after it succeeds. There is
    // deliberately no `onblur` on the input that could re-fire this: Enter
    // and Escape already close the field themselves, and removing a focused
    // element fires a trailing blur afterwards. A blur-commit would re-save
    // whatever text was last typed even after Escape discarded it -- see
    // commitNote's comment for the full failure mode, which jsdom cannot
    // catch (it does not fire blur-on-removal), so this shape is avoided
    // entirely rather than guarded against.
    editingName = false
    mutate(() => api.renameReel(slug, name), "Couldn't rename this reel")
  }

  function onNameKey(e: KeyboardEvent): void {
    if (e.key === 'Enter') {
      e.preventDefault()
      saveName()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      // Cancel, not commit: nameBuffer is simply dropped.
      editingName = false
    }
  }

  // "Where is the file" for the render itself, mirroring the session page's
  // clips panel: rendered_path was dead text here while the answer sat one
  // POST away. Not routed through `mutate` -- revealing changes no server
  // state and must work even while a mutation is in flight.
  function revealRendered(): void {
    const relpath = detail?.reel.rendered_path
    if (!relpath) return
    api
      .revealClip(relpath)
      .then((r) => {
        if (!r.ok) toaster.push(r.reason ?? "Couldn't reveal the file.")
      })
      .catch((e) => toaster.push(`Couldn't reveal the file -- ${String(e)}`))
  }

  function confirmDelete(): void {
    confirmingDelete = false
    // Routed through `mutate` like every other action on this page, so a
    // fast double-click on "Yes, delete" can't fire two DELETEs -- harmless
    // server-side (the second just 404s), but the first press already
    // disables the button via `busy`, same as everywhere else.
    mutate(async () => {
      await api.deleteReel(slug)
      // No toast: the page itself is about to disappear, and a toast on a
      // page nobody is looking at any more would be pointless.
      navigate('/reels')
    }, "Couldn't delete this reel")
  }
</script>

<div class="mb-6">
  <button class="font-data text-caption text-faint hover:text-dim" onclick={() => navigate('/reels')}>
    Reels
  </button>
  <span class="font-data text-caption text-faint"> › </span>
  {#if editingName}
    <div class="mt-1 flex items-center gap-2">
      <input
        data-rename-input
        class="rounded border border-line bg-surface px-2 py-1 text-display
               font-semibold"
        bind:value={nameBuffer}
        onkeydown={onNameKey}
        aria-label="Reel name"
      />
      <!-- No onblur here at all -- see saveName's comment. Save and
           Cancel are the only ways this field closes besides the keys
           onNameKey already handles. -->
      <button
        data-rename-save
        class="rounded border border-line px-2 py-1 font-data text-data
               text-fg hover:bg-surface-2 disabled:cursor-not-allowed
               disabled:opacity-40 motion-safe:transition-colors"
        disabled={busy || normalizedReelName(nameBuffer) === null}
        onclick={saveName}
      >Save</button>
      <button
        class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
        onclick={cancelRename}
      >Cancel</button>
    </div>
  {:else}
    <h1 class="mt-1 flex items-center gap-2 text-display font-semibold">
      {detail?.reel.name ?? slug}
      {#if detail}
        <button
          data-rename
          class="font-data text-data font-normal text-dim hover:text-fg motion-safe:transition-colors"
          onclick={startRename}
        >rename</button>
      {/if}
    </h1>
  {/if}
</div>

{#if error}
  <ErrorNote {error} subject="reel" />
{:else if loading && !detail}
  <div class="flex justify-center py-12">
    <Mark size={40} state="loading" />
  </div>
{:else if detail}
  <!-- rounded-lg border bg-surface, not a bare flex row: this bar carries
       `dim`/`faint` text (the numbered-render label, the clip count, the
       delete-confirmation "Cancel") that measures under 4.5:1 over browse's
       scrim mid-band -- see the header comment in Setup.svelte for the same
       rule. The bordered buttons already look like a toolbar; giving the
       bar itself a surface just makes that literal. -->
  <div class="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-3">
    <button
      class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
             hover:bg-surface-2 motion-safe:transition-colors"
      onclick={() => (showPicker = !showPicker)}
    >Add rallies</button>

    <button
      data-preview
      class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
             hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
      disabled={items.length === 0}
      onclick={() => (showPreview = !showPreview)}
    >{showPreview ? 'Hide preview' : 'Preview'}</button>

    {#if canWatch}
      <!-- Only offered once rendered_path is set, dirty or not: the file on
           disk from the last render is still watchable regardless of
           whether the current membership still matches it (canWatchRendered
           in lib/reels.ts). Absent rather than merely disabled -- there is
           nothing behind the button before a first render. -->
      <button
        data-watch
        class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
               hover:bg-surface-2 motion-safe:transition-colors"
        onclick={() => (showWatch = !showWatch)}
      >{showWatch ? 'Hide watch' : 'Watch'}</button>
    {/if}

    <!-- Cutting is the ONLY action here that starts an encode. Render never
         enqueues clips: a button labelled "render" must not silently launch
         half an hour of work. Both are also disabled while `busy`: they now
         go through the same one-in-flight `mutate` as every other action
         here, and disabling is how a click while busy avoids being a
         silent no-op -- the user sees why nothing happened instead of
         wondering whether the click registered. -->
    <button
      data-cut
      class="rounded border border-line px-3 py-1.5 font-data text-data text-fg
             hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
      disabled={items.length === 0 || busy}
      onclick={cutMissing}
    >{busy ? 'Working…' : 'Cut missing clips'}</button>

    <!-- The one filled button in this row: rendering is the page's primary
         action and was invisible among five identical outlined buttons.
         The filled-button idiom, app-wide: bg-fg with an explicit text-bg.
         It replaces a filled accent that needed the same explicit text-bg
         for a different reason (a white label on that blue measured ~2:1);
         here the pairing is correct by construction. -->
    <button
      data-render
      class="rounded bg-fg px-3 py-1.5 font-data text-data font-medium text-bg
             hover:bg-fg/90 disabled:cursor-not-allowed disabled:opacity-40
             motion-safe:transition-colors"
      disabled={blocked !== null || busy}
      title={blocked ?? ''}
      onclick={render}
    >{busy ? 'Working…' : blocked ? `Render — ${blocked}` : 'Render'}</button>

    <!-- Off by default -- plain render is the fast -c copy path, and
         checking this trades that for a full re-encode of every clip
         (~4-8x footage duration) so the counter and each item's note can be
         burned into the frame. -->
    <label
      class="flex items-center gap-1.5 font-data text-data text-dim"
      title="Burn in a clip counter and each clip's note. Re-encodes every clip -- 4-8x footage duration, instead of the fast plain render."
    >
      <input
        data-numbered
        type="checkbox"
        bind:checked={numbered}
      />
      numbered
    </label>
    <!-- Heart-rate overlay from RallyMetrics. Disabled, not hidden, when no
         folder is set or the drive is off: the label's title says which. -->
    <label
      class="flex items-center gap-1.5 font-data text-data {hrAvailable ? 'text-dim' : 'text-faint'}"
      title={hrRoot === null
        ? 'No RallyMetrics clips folder set. Run: splitstep config set-hr-clips PATH'
        : hrAvailable
          ? `Use RallyMetrics's heart-rate-overlaid copies of every clip (${hrRoot}). Refused if any clip has no copy yet.`
          : `RallyMetrics clips folder is not available right now: ${hrRoot}`}
    >
      <input
        data-hr
        type="checkbox"
        bind:checked={hr}
        disabled={!hrAvailable}
      />
      heart rate
    </label>

    {#if confirmingDelete}
      <!-- Inline, not a browser confirm(): the confirming press must name
           what it destroys, which a confirm() dialog cannot show with any
           formatting, and a stray Enter on the page cannot dismiss and
           accidentally confirm it the way a native dialog's default button
           could. -->
      <span data-delete-confirm class="flex items-center gap-2 font-data text-data text-danger">
        {deleteConfirmationText(detail.reel.name, detail.reel.rendered_bytes)}
        <button
          data-delete-confirm-yes
          class="rounded border border-danger px-2 py-1 text-danger hover:bg-danger/40
                 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
          disabled={busy}
          onclick={confirmDelete}
        >Yes, delete</button>
        <button
          class="text-dim hover:text-fg motion-safe:transition-colors"
          onclick={() => (confirmingDelete = false)}
        >Cancel</button>
      </span>
    {:else}
      <button
        data-delete
        class="rounded border border-danger/60 px-3 py-1.5 font-data text-data text-danger
               hover:bg-danger/40 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:transition-colors"
        disabled={busy}
        onclick={() => (confirmingDelete = true)}
      >Delete reel</button>
    {/if}

    <span class="ml-auto flex items-center gap-2 font-data text-data text-faint">
      {items.length} clips{detail.reel.rendered_path && !detail.reel.dirty
        ? ` · ${detail.reel.rendered_path}`
        : ''}
      <!-- Visible whenever a rendered file exists, dirty or not -- a stale
           render is still a real file someone may want to grab. -->
      {#if detail.reel.rendered_path}
        <!-- text-fg, not text-dim: it sat unnoticed in gray beside the
             equally gray path text (2026-08-26). -->
        <button
          data-reveal-rendered
          class="text-fg hover:underline motion-safe:transition-colors"
          onclick={revealRendered}
        >reveal file</button>
      {/if}
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

  {#if showWatch && canWatch && detail.reel.rendered_path}
    <!-- bg-surface added to the existing border: this panel carries `dim`
         text (the rendered-file caption below) and a bare border with no
         fill left it sitting straight on the court, same failure as the
         action bar above. -->
    <div class="mb-4 rounded-lg border border-line bg-surface p-4">
      <div class="mb-3 flex items-baseline justify-between">
        <h2 class="text-body font-semibold">Watch</h2>
        <div class="flex items-center gap-4 font-data text-data text-dim">
          <!-- Preview above seeks the 1080p proxy to each span in order --
               it shows TIMING, and by design cannot reveal a -c copy
               artifact (a mismatched profile that -c copy stitched without
               re-encoding). This plays the actual rendered 4K file, the
               only way to see one. When dirty, say so plainly rather than
               silently showing a file that may no longer match the reel's
               current membership -- mark_rendered leaves rendered_path set
               across a membership change on purpose (see its docstring). -->
          <span>
            {detail.reel.dirty
              ? 'rendered 4K · last render, not current membership'
              : 'rendered 4K file'}
          </span>
          <button class="hover:text-fg motion-safe:transition-colors" onclick={() => (showWatch = false)}>
            close
          </button>
        </div>
      </div>
      <!-- No <track kind="captions">: this is unscripted rally footage with
           no dialogue and no caption source to author one from, so there is
           nothing honest a <track> could contain. `muted={false}` is not a
           lint workaround bolted on top -- it is the same explicit,
           accurate statement of playback state VideoDeck.svelte already
           makes for this exact class of content, and it is what tells
           svelte-check's a11y-media-has-caption check this was a deliberate
           call rather than an oversight, without a blanket ignore comment. -->
      <video
        controls
        muted={false}
        class="aspect-video w-full rounded bg-black"
        src={api.reelUrl(detail.reel.slug)}
      ></video>
    </div>
  {/if}

  {#if items.length === 0}
    <!-- bg-surface, not bare -- see Reels.svelte's empty state for the same
         rule and reasoning. -->
    <p class="rounded-xl border border-line bg-surface p-4 text-body text-dim">
      Nothing in this reel yet. Add rallies above, or compile a session's points from the
      end of its review queue.
    </p>
  {:else}
    <ReelItemList {items} oncommit={commitOrder} onremove={remove} onnote={setItemNote} />
  {/if}
{/if}

{#if toaster.toasts.length > 0}
  <div class="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col
              items-center gap-2">
    {#each toaster.toasts as t (t.id)}
      <p class="rounded-full px-4 py-2 font-data text-data {toastToneClasses(t.tone)}">
        {t.message}
      </p>
    {/each}
  </div>
{/if}
