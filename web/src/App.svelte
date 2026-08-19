<script lang="ts">
  import { createRouter } from './lib/router.svelte'

  const router = createRouter()
</script>

<main class="mx-auto max-w-6xl p-6">
  {#if router.current.name === 'library'}
    <h1 class="text-xl font-semibold">Library</h1>
  {:else}
    <h1 class="text-xl font-semibold">Session {router.current.id}</h1>
  {/if}
</main>
