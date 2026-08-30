<script lang="ts">
  import { api } from '../lib/api'

  interface Props {
    /** Null while the session has no source, or the reel no clips. */
    session_id: string | null
    idx: number | null
    atMs?: number
    alt: string
  }
  let { session_id, idx, atMs = 30_000, alt }: Props = $props()

  // Two ways to end up with no picture, and they are not the same thing: no
  // source at all (nothing to ask for), and a source whose proxy has not
  // been built yet (frame.jpg 404s until handle_build_proxy finishes). Both
  // land on the same placeholder, so the card keeps its shape either way and
  // the row height does not jump as a session moves through the pipeline.
  let broken = $state(false)
  const src = $derived(
    session_id !== null && idx !== null ? api.frameUrl(session_id, idx, atMs) : null,
  )

  // A new src is a new attempt: without this a card that failed once stays
  // on the placeholder after the proxy lands and the poll re-renders it.
  $effect(() => {
    src
    broken = false
  })
</script>

<div class="aspect-video w-38 min-[1800px]:w-70 shrink-0 overflow-hidden rounded bg-black">
  {#if src && !broken}
    <img
      {src}
      {alt}
      class="h-full w-full object-cover"
      loading="lazy"
      decoding="async"
      onerror={() => (broken = true)}
    />
  {:else}
    <!-- Not an icon or a "no image" string: the card is a row of real
         stills, and a glyph among them reads as a broken one. A flat panel
         in the surface colour reads as "not yet", which is what it means. -->
    <div class="h-full w-full bg-surface-2"></div>
  {/if}
</div>
