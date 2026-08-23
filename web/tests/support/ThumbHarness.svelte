<script lang="ts">
  // Same reason SessionHarness and SetupHarness exist: `mount()` can only
  // update a mounted component's props from outside when they are backed by
  // `$state`, and a .test.ts cannot declare one. This one exists so the
  // "tries again when the source changes" case can actually change it.
  import { untrack } from 'svelte'
  import Thumb from '../../src/components/Thumb.svelte'

  let { idx: initial }: { idx: number | null } = $props()

  // A one-time seed: this harness owns `idx` after mount and the test drives
  // it through setIdx. `untrack` says that is deliberate rather than an
  // accidentally non-reactive read, the same way QueueMode marks its
  // one-time snapshot of `detail.rallies`.
  let idx = $state<number | null>(untrack(() => initial))

  export function setIdx(next: number | null): void {
    idx = next
  }
</script>

<Thumb session_id="s1" {idx} alt="test" />
