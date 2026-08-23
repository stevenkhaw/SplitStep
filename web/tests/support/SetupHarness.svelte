<script lang="ts">
  // The Setup.svelte counterpart to SessionHarness, and it exists for the
  // same reason: Svelte 5's `mount()` can only update a mounted component's
  // props from outside when they are backed by `$state`, and `$state` is only
  // legal inside a .svelte/.svelte.ts file. setup-wizard.test.ts mounts Setup
  // directly because it never changes the id; route-effect-staleness.test.ts
  // does, so it needs this.
  //
  // App.svelte renders `<Setup id={router.current.id} />` unkeyed, so
  // navigating from one /setup/<id> to another swaps this prop on the live
  // instance rather than remounting -- which is exactly the situation the
  // tests below cover.
  import Setup from '../../src/routes/Setup.svelte'

  let id = $state('src1')

  export function setId(newId: string): void {
    id = newId
  }
</script>

<Setup {id} />
