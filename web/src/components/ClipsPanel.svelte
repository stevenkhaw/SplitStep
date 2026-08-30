<script lang="ts">
  import { api } from '../lib/api'
  import {
    clipKey,
    clipLabel,
    clipMediaUrl,
    clipPollTarget,
    clipRevealRelpath,
    formatClipSize,
    groupClipsBySource,
    sessionClipsRelpath,
    shouldPollForExport,
  } from '../lib/clips'
  import { describeApiError } from '../lib/errors'
  import { startPolling } from '../lib/polling'
  import { createToaster, toastToneClasses } from '../lib/toaster.svelte'
  import { untrack } from 'svelte'
  import type { Clip, ExportResult } from '../lib/types'

  interface Props {
    sessionId: string
    /** A fresh object every time QueueMode's export buttons successfully
     *  kick off a cut (see Session.svelte's `onexport`) -- `null` before
     *  the first one. Read for its object identity, not its values: two
     *  exports can report an identical `{queued:0,...}` shape, and each one
     *  is still a distinct event this panel should react to. */
    exportResult?: ExportResult | null
  }

  let { sessionId, exportResult = null }: Props = $props()

  let open = $state(false)
  let clips = $state<Clip[]>([])
  let error = $state<unknown>(null)
  // Which clip's inline <video> is expanded, or null for none. Span-derived
  // (clipKey), not an index -- a refetch hands back a fresh array of fresh
  // objects, and an index would point at whatever now sits in that slot
  // instead of staying attached to the clip the user actually opened.
  let expandedKey = $state<string | null>(null)
  // Set only while an export this panel witnessed is still short of the
  // clip count it should produce -- see clipPollTarget/shouldPollForExport
  // in lib/clips.ts. `null` means "nothing to poll toward".
  let pollTarget = $state<number | null>(null)

  const toaster = createToaster()

  // Out-of-order guard for the plain (non-polling) fetches below: `open`
  // toggling and a fresh `exportResult` can each trigger their own
  // fetchClips() call close together, and a slower one of the two must not
  // overwrite what a faster, later one already landed.
  let fetchSeq = 0
  function fetchClips(): void {
    const seq = ++fetchSeq
    api
      .getSessionClips(sessionId)
      .then((c) => {
        if (seq !== fetchSeq) return
        clips = c
        error = null
      })
      .catch((e) => {
        if (seq !== fetchSeq) return
        error = e
      })
  }

  // Fetches on mount, and again on every later change of `sessionId` --
  // Session.svelte renders `<Session id=…>` unkeyed (see App.svelte and
  // CLAUDE.md's route-effect-staleness rule), so navigating straight from
  // one session's page to another reuses this component instance rather
  // than remounting it. Reset up front, same shape as Session.svelte's own
  // load effect: `fetchSeq` already discards a stale response, but clearing
  // `clips` here too stops the *previous* session's clip list from staying
  // on screen, under the new session's header, for the length of the fetch.
  // `pollTarget`/`expandedKey` are reset for the same reason -- a poll
  // target or an expanded video is meaningless once the session underneath
  // it has changed.
  $effect(() => {
    clips = []
    error = null
    pollTarget = null
    expandedKey = null
    fetchClips()
  })

  // Re-fetch when the user actually opens the panel, in case it has sat
  // mounted-but-collapsed long enough for the on-disk list to have moved on
  // since the mount fetch above.
  $effect(() => {
    if (!open) return
    fetchClips()
  })

  // Fires once per distinct export kickoff (see the Props doc above for why
  // this compares by identity). Opens the panel and arms a poll target --
  // the derived `isExporting`/poll effect below does the actual polling.
  $effect(() => {
    const result = exportResult
    if (!result) return
    open = true
    pollTarget = clipPollTarget(untrack(() => clips.length), result)
    // Also fetch right away rather than waiting for the first poll tick.
    // This can race the `open` effect above (both fire when `open` flips
    // false -> true) into firing fetchClips() twice back to back -- fine:
    // it's one extra cheap directory walk, and fetchSeq keeps whichever
    // response is actually last from being clobbered by the other.
    fetchClips()
  })

  // Poll only while short of the target, exactly like Reel.svelte's own
  // `shouldPollReel` guard: read through `$derived` so the effect below
  // reacts to a genuine true<->false flip rather than to every fresh
  // `clips` array a poll (or an unrelated refetch) produces.
  const isExporting = $derived(shouldPollForExport(clips.length, pollTarget))

  // 3s, matching JobsBadge -- unlike Reel.svelte's 15s reel-render poll,
  // clip cuts run in the same single-threaded queue whose progress the jobs
  // badge already reports at that cadence, so this panel matching it keeps
  // the two numbers on screen (badge percentage, clip count here) moving in
  // step instead of visibly disagreeing about how fresh they are.
  const CLIPS_EXPORT_POLL_MS = 3000

  $effect(() => {
    if (!isExporting) return
    const currentSessionId = sessionId
    const poller = startPolling(async () => {
      try {
        clips = await api.getSessionClips(currentSessionId)
      } catch {
        // A background refresh failing is not worth a toast for a panel
        // nobody may currently be watching -- the next tick just retries.
      }
    }, CLIPS_EXPORT_POLL_MS)
    return () => poller.stop()
  })

  function toggleWatch(key: string): void {
    // At most one mounted <video> at a time: swapping to a different key
    // (or to null) tears down whichever was expanded, rather than letting N
    // 4K <video> elements accumulate.
    expandedKey = expandedKey === key ? null : key
  }

  async function reveal(relpath: string): Promise<void> {
    try {
      const result = await api.revealClip(relpath)
      if (!result.ok) toaster.push(result.reason ?? 'Could not reveal in Finder.')
    } catch (e) {
      toaster.push(describeApiError(e, 'clip').message)
    }
  }
</script>

<!--
  A plain wrapper around <details>, not <details> itself, because the toast
  row below must stay visible while the panel is COLLAPSED: "Reveal folder"
  in the summary is deliberately reachable without expanding the panel (see
  its preventDefault comment below), and a native <details> hides every
  child but its <summary> when closed -- a toast nested inside it would
  report that button's failure into a pane the reviewer cannot see.
-->
<div class="mt-6">
  <details bind:open class="rounded-lg border border-line">
    <summary class="flex cursor-pointer select-none items-center justify-between gap-3 p-4">
      <span class="text-body font-semibold">
        Clips
        {#if clips.length > 0}
          <span class="font-data text-data text-dim">({clips.length})</span>
        {/if}
      </span>
      <!-- `preventDefault`, not `stopPropagation`: clicking anywhere inside
           a <summary> toggles the parent <details> as the browser's
           *default* action for that click, not via bubbling from a
           listener this could intercept -- preventDefault on the click is
           what a button nested in a <summary> needs to act on its own
           without also opening/closing the panel. -->
      <!-- text-fg, not text-dim: gray text-buttons disappear next to the
           filenames beside them. There is no accent any more, so contrast
           rather than hue is what makes a text button look pressable. -->
      <button
        class="shrink-0 rounded border border-line px-2 py-1 font-data text-caption text-fg
               hover:bg-surface-2 hover:brightness-110 motion-safe:transition-colors"
        onclick={(e) => {
          e.preventDefault()
          reveal(sessionClipsRelpath(sessionId))
        }}
      >Reveal folder</button>
    </summary>

    <div class="px-4 pb-4">
      {#if error}
        <p class="text-caption text-danger">{describeApiError(error, 'session').message}</p>
      {:else if clips.length === 0}
        <p class="text-caption text-dim">
          No clips cut yet. Export point or starred clips from the end of the review queue.
        </p>
      {:else}
        <div class="space-y-4">
          {#each groupClipsBySource(clips) as group (group.source_idx)}
            <div>
              <h3 class="mb-1 font-data text-data text-faint">
                Video {String(group.source_idx).padStart(2, '0')}
              </h3>
              <ul class="divide-y divide-line">
                {#each group.clips as clip (clipKey(clip))}
                  <li class="py-2">
                    <div class="flex items-center gap-3">
                      <span class="font-data text-data tabular-nums text-dim">{clipLabel(clip)}</span>
                      <span class="font-data text-data text-faint">{formatClipSize(clip.size_bytes)}</span>
                      <span class="ml-auto flex shrink-0 items-center gap-2">
                        <button
                          class="rounded border border-line px-2 py-1 font-data text-caption text-fg
                                 hover:bg-surface-2 motion-safe:transition-colors"
                          onclick={() => toggleWatch(clipKey(clip))}
                        >{expandedKey === clipKey(clip) ? 'Hide' : 'Watch'}</button>
                        <button
                          class="rounded border border-line px-2 py-1 font-data text-caption
                                 text-fg hover:bg-surface-2 hover:brightness-110
                                 motion-safe:transition-colors"
                          onclick={() => reveal(clipRevealRelpath(sessionId, clip))}
                        >Reveal</button>
                      </span>
                    </div>
                    {#if expandedKey === clipKey(clip)}
                      <!-- No <track kind="captions">: this is unscripted
                           rally footage with no dialogue and no caption
                           source to author one from, so there is nothing
                           honest a <track> could contain. `muted={false}`
                           is not a lint workaround bolted on top -- it is
                           the same explicit, accurate statement of playback
                           state Reel.svelte's watch video already makes for
                           this exact class of content, and it is what tells
                           svelte-check's a11y-media-has-caption check this
                           was a deliberate call rather than an oversight,
                           without a blanket ignore comment. -->
                      <video
                        controls
                        muted={false}
                        class="mt-2 aspect-video w-full rounded bg-black"
                        src={clipMediaUrl(sessionId, clip)}
                      ></video>
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </details>

  {#if toaster.toasts.length > 0}
    <div class="mt-2 space-y-1">
      {#each toaster.toasts as t (t.id)}
        <div class="rounded {toastToneClasses(t.tone, 'muted')} px-3 py-2 text-body">
          {t.message}
        </div>
      {/each}
    </div>
  {/if}
</div>
