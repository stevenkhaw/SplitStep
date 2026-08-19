<script lang="ts">
  interface Props {
    id: string
  }
  let { id }: Props = $props()
</script>

<h1 class="text-xl font-semibold">Session {id}</h1>
