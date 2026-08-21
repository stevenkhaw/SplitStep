<script lang="ts">
  import { untrack } from 'svelte'
  import { api } from '../lib/api'
  import { ReelPreviewController } from '../lib/reelPreview'
  import type { ReelItem } from '../lib/types'
  import VideoDeck from './VideoDeck.svelte'

  interface Props {
    items: ReelItem[]
    onclose: () => void
  }

  let { items, onclose }: Props = $props()

  // One-time snapshot, like QueueMode's QueueController: the parent mounts
  // this inside a {#key} on the item list, so a membership change remounts
  // it fresh rather than mutating a preview mid-playback.
  const preview = new ReelPreviewController(untrack(() => items))
  let version = $state(0) // bumped to re-read the controller after a mutation

  // `version` is the dependency that forces the re-read -- the controller is
  // a plain class, so a getter read registers no signal and the template
  // would render once and freeze. Same pattern QueueMode uses.
  const current = $derived.by(() => {
    version
    return preview.current
  })
  const next = $derived.by(() => {
    version
    return preview.next
  })
  const position = $derived.by(() => {
    version
    return { index: preview.index, total: preview.total, finished: preview.finished }
  })

  const proxy = (item: ReelItem) => api.proxyUrl(item.session_id, item.source_idx)

  function onended(): void {
    preview.advance()
    version += 1
  }

  function restart(): void {
    preview.restart()
    version += 1
  }
</script>

<div class="rounded-lg border border-neutral-800 p-4">
  <div class="mb-3 flex items-baseline justify-between">
    <h2 class="text-sm font-semibold">Preview</h2>
    <div class="flex items-center gap-4 font-mono text-xs text-neutral-400">
      <!-- Stated rather than discovered later: this is the 1080p proxy, and
           it cannot reveal a -c copy artifact. It shows TIMING exactly. -->
      <span>1080p proxy · timing only</span>
      <span class="tabular-nums">
        {Math.min(position.index + 1, position.total)} / {position.total}
      </span>
      <button class="hover:text-neutral-200" onclick={onclose}>close</button>
    </div>
  </div>

  {#if current}
    <VideoDeck
      src={proxy(current)}
      startMs={current.start_ms}
      endMs={current.end_ms}
      nextSrc={next ? proxy(next) : undefined}
      nextStartMs={next?.start_ms}
      {onended}
    />
  {:else}
    <div class="flex h-40 flex-col items-center justify-center gap-3 rounded bg-black">
      <p class="font-mono text-xs text-neutral-400">
        {position.total === 0 ? 'Nothing in this reel yet.' : 'End of reel.'}
      </p>
      {#if position.total > 0}
        <button
          class="rounded border border-neutral-700 px-3 py-1.5 font-mono text-xs
                 text-neutral-200 hover:bg-neutral-800"
          onclick={restart}
        >
          Play again
        </button>
      {/if}
    </div>
  {/if}
</div>
