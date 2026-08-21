<!-- web/src/routes/Reel.svelte -- replaced wholesale in Task 9 -->
<script lang="ts">
  let { slug }: { slug: string } = $props()
</script>

<p class="text-sm text-neutral-400">Loading {slug}…</p>
