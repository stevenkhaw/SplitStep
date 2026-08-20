<script lang="ts">
  import Library from './routes/Library.svelte'
  import Session from './routes/Session.svelte'
  import Setup from './routes/Setup.svelte'
  import { createRouter } from './lib/router.svelte'

  const router = createRouter()
</script>

<main class="mx-auto max-w-6xl p-6">
  {#if router.current.name === 'library'}
    <Library />
  {:else if router.current.name === 'setup'}
    <Setup id={router.current.id} />
  {:else}
    <Session id={router.current.id} />
  {/if}
</main>
