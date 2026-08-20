<script lang="ts">
  import Library from './routes/Library.svelte'
  import Session from './routes/Session.svelte'
  import { createRouter } from './lib/router.svelte'

  const router = createRouter()
</script>

<main class="mx-auto max-w-6xl p-6">
  {#if router.current.name === 'library'}
    <Library />
  {:else}
    <Session id={router.current.id} />
  {/if}
</main>
