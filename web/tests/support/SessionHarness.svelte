<script lang="ts">
  // A tiny reactive host for Session.svelte, used only by
  // requeue-on-resegment.test.ts. Svelte 5's `mount()` API can only update a
  // mounted component's props from outside if they're backed by `$state`,
  // and `$state` only works inside a .svelte/.svelte.ts file -- a plain
  // .test.ts cannot declare it. This harness exists purely to give the test
  // a `$state` id it can flip, exposed via an exported function (the same
  // mechanism VideoDeck/ZoomBand expose imperative methods through).
  import Session from '../../src/routes/Session.svelte'

  let id = $state('s1')

  export function setId(newId: string): void {
    id = newId
  }
</script>

<Session {id} />
