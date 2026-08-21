<script lang="ts">
  // A reactive host for VideoDeck, mirroring SessionHarness's purpose:
  // Svelte 5's `mount()` can only update a mounted component's props from
  // outside if they are backed by `$state`, and `$state` only works inside a
  // .svelte/.svelte.ts file -- a plain .test.ts cannot declare it. Advancing
  // the queue is exactly a props change, so a test cannot exercise the
  // element swap without this.
  import { untrack } from 'svelte'
  import VideoDeck from '../../src/components/VideoDeck.svelte'

  interface Props {
    src: string
    startMs: number
    endMs: number
    nextSrc?: string
    nextStartMs?: number
    onended: () => void
  }
  let { src, startMs, endMs, nextSrc, nextStartMs, onended }: Props = $props()

  // A one-time snapshot of the incoming props: this harness owns the rally
  // from mount onward and `advance` is the only thing that changes it, so
  // reading them reactively here would be wrong as well as noisy.
  let current = $state(untrack(() => ({ src, startMs, endMs, nextSrc, nextStartMs })))

  export function advance(to: {
    src: string
    startMs: number
    endMs: number
    nextSrc?: string
    nextStartMs?: number
  }): void {
    // Spelled out rather than assigned wholesale: `current`'s snapshot type
    // has nextSrc/nextStartMs present-but-undefined, while `to` has them
    // optional, and the two are not assignable.
    current = {
      src: to.src,
      startMs: to.startMs,
      endMs: to.endMs,
      nextSrc: to.nextSrc,
      nextStartMs: to.nextStartMs,
    }
  }
</script>

<VideoDeck
  src={current.src}
  startMs={current.startMs}
  endMs={current.endMs}
  nextSrc={current.nextSrc}
  nextStartMs={current.nextStartMs}
  {onended}
/>
