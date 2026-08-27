<script lang="ts">
  import ErrorNote from './ErrorNote.svelte'
  import { api } from '../lib/api'
  import { firstRunVisible, nextStep, prevStep, type FirstRunStep } from '../lib/firstrun'

  interface Props {
    sessionCount: number
    loading: boolean
  }
  let { sessionCount, loading }: Props = $props()

  let step = $state<FirstRunStep>('welcome')
  let dragOver = $state(false)
  let uploading = $state(false)
  let error = $state<unknown>(null)
  let fileInput = $state<HTMLInputElement>()

  async function upload(files: FileList | null | undefined) {
    const file = files?.[0]
    if (!file || uploading) return
    uploading = true
    error = null
    try {
      await api.importFile(file)
      // The watcher takes over from here; the card's job is done as soon as
      // the session poll sees the ingest.
      step = 'waiting'
    } catch (e) {
      error = e
    } finally {
      uploading = false
    }
  }
</script>

{#if firstRunVisible(sessionCount, loading)}
  <section class="mx-auto max-w-xl rounded-xl border border-line bg-surface p-8 text-center">
    {#if step === 'welcome'}
      <h2 class="text-title font-semibold">Welcome to SplitStep</h2>
      <p class="mt-3 text-body text-dim">
        SplitStep turns a phone recording of your tennis session into reviewed
        rally clips: drop in footage, outline your court once, and review the
        cuts with the keyboard.
      </p>
      <button
        type="button"
        class="mt-6 rounded-lg bg-accent px-4 py-2 text-body font-semibold text-bg"
        onclick={() => (step = nextStep(step))}
      >
        Get started
      </button>
    {:else if step === 'tips'}
      <h2 class="text-title font-semibold">Filming tips</h2>
      <p class="mt-3 text-body text-dim">
        Best results: mount the phone on the fence behind the court when the
        court allows it. Record 4K at 30 fps, and leave <strong class="text-fg">HDR on</strong>.
      </p>
      <div class="mt-6 flex items-center justify-center gap-4">
        <button
          type="button"
          class="text-body text-accent hover:underline"
          onclick={() => (step = prevStep(step))}
        >
          Back
        </button>
        <button
          type="button"
          class="rounded-lg bg-accent px-4 py-2 text-body font-semibold text-bg"
          onclick={() => (step = nextStep(step))}
        >
          Next
        </button>
      </div>
    {:else if step === 'drop'}
      <h2 class="text-title font-semibold">Add your first video</h2>
      <!-- The drop target is a real button (file picker) that also accepts a
           drag: one element, both gestures, keyboard reachable. -->
      <button
        type="button"
        class="mt-4 w-full rounded-lg border-2 border-dashed p-10 text-body
               {dragOver ? 'border-accent text-fg' : 'border-line text-dim'}"
        disabled={uploading}
        ondragover={(e) => {
          e.preventDefault()
          dragOver = true
        }}
        ondragleave={() => (dragOver = false)}
        ondrop={(e) => {
          e.preventDefault()
          dragOver = false
          upload(e.dataTransfer?.files)
        }}
        onclick={() => fileInput?.click()}
      >
        {uploading ? 'Uploading…' : 'Drop your video here, or click to choose one'}
      </button>
      <input
        bind:this={fileInput}
        type="file"
        accept="video/*,.mov,.mp4,.m4v,.avi,.mkv"
        class="hidden"
        onchange={(e) => upload(e.currentTarget.files)}
      />
      <p class="mt-3 text-caption text-dim">
        We'll ask you to outline your court (~20 seconds), then processing
        takes ~20 minutes.
      </p>
      <p class="mt-1 text-caption text-faint">
        or drop files into the library's <code>_inbox/</code> folder
      </p>
      {#if error}
        <div class="mt-3 text-left">
          <ErrorNote {error} subject="upload" />
        </div>
      {/if}
      <button
        type="button"
        class="mt-4 text-body text-accent hover:underline"
        onclick={() => (step = prevStep(step))}
      >
        Back
      </button>
    {:else}
      <h2 class="text-title font-semibold">Reading your footage</h2>
      <p class="mt-3 text-body text-dim">
        This list updates on its own — your session will appear here in a
        moment, then ask for a quick court setup.
      </p>
    {/if}
  </section>
{/if}
