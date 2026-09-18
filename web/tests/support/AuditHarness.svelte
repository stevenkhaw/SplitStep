<script lang="ts">
  // The Audit.svelte counterpart to SessionHarness/SetupHarness, for the same
  // reason: mount() can only swap a prop from outside when it is backed by
  // `$state`, and App.svelte renders `<Audit id={router.current.id} />`
  // unkeyed, so navigating between two sources swaps the prop on the live
  // instance.
  import Audit from '../../src/routes/Audit.svelte'

  let id = $state('src1')

  export function setId(newId: string): void {
    id = newId
  }
</script>

<Audit {id} />
