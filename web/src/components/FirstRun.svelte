<script lang="ts">
  import ErrorNote from './ErrorNote.svelte'
  import { api } from '../lib/api'

  // Visibility is owned by Library.svelte's empty-state branch -- this card
  // IS the empty state, so a second gate in here would just be the same
  // condition written twice. Four literal steps, advanced by the buttons
  // that name them; 'waiting' ends not by advancing but by Library's
  // session poll replacing the card once the first ingest lands.
  type Step = 'welcome' | 'tips' | 'drop' | 'waiting'
  let step = $state<Step>('welcome')
  let dragOver = $state(false)
  let uploading = $state(false)
  let error = $state<unknown>(null)
  let fileInput = $state<HTMLInputElement>()

  async function upload(files: File[]) {
    if (files.length === 0 || uploading) return
    uploading = true
    error = null
    let imported = 0
    try {
      // Every file, not files[0]: a friend drags a whole session's clips at
      // once, and silently importing one of three would read as footage
      // gone missing. Sequential on purpose -- the server streams each to
      // disk, and one failure still lets the rest land.
      for (const file of files) {
        try {
          await api.importFile(file)
          imported += 1
        } catch (e) {
          error = e
        }
      }
      if (imported > 0) step = 'waiting'
    } finally {
      uploading = false
    }
  }

  function fromInput(e: Event & { currentTarget: HTMLInputElement }) {
    const files = Array.from(e.currentTarget.files ?? [])
    // Clear before uploading: a value left behind means re-picking the same
    // file after a failure fires no change event and the picker looks dead.
    e.currentTarget.value = ''
    upload(files)
  }
</script>

<!-- Without this, a drop that misses the dashed target -- or lands on any
     other step -- hits the browser's default handling, which navigates the
     tab to the video file and destroys the app mid-onboarding. -->
<svelte:window
  ondragover={(e) => e.preventDefault()}
  ondrop={(e) => e.preventDefault()}
/>

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
      class="mt-6 rounded-lg bg-fg px-4 py-2 text-body font-semibold text-bg"
      onclick={() => (step = 'tips')}
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
        class="text-body text-fg hover:underline"
        onclick={() => (step = 'welcome')}
      >
        Back
      </button>
      <button
        type="button"
        class="rounded-lg bg-fg px-4 py-2 text-body font-semibold text-bg"
        onclick={() => (step = 'drop')}
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
             {dragOver ? 'border-fg text-fg' : 'border-line text-dim'}"
      disabled={uploading}
      ondragover={(e) => {
        e.preventDefault()
        dragOver = true
      }}
      ondragleave={() => (dragOver = false)}
      ondrop={(e) => {
        e.preventDefault()
        dragOver = false
        upload(Array.from(e.dataTransfer?.files ?? []))
      }}
      onclick={() => fileInput?.click()}
    >
      {uploading ? 'Uploading…' : 'Drop your videos here, or click to choose'}
    </button>
    <!-- accept mirrors VIDEO_SUFFIXES in splitstep/watcher.py -- the server
         is the authority (its 415 names the real list); this only pre-greys
         the picker, so drift here is cosmetic, not a gate. -->
    <input
      bind:this={fileInput}
      type="file"
      multiple
      accept="video/*,.mov,.mp4,.m4v,.avi,.mkv"
      class="hidden"
      onchange={fromInput}
    />
    <p class="mt-3 text-caption text-dim">
      We'll ask you to outline your court (~20 seconds), then processing
      takes ~20 minutes.
    </p>
    <p class="mt-1 text-caption text-faint">
      or drop files into the library's <code>_inbox/</code> folder
    </p>
    <button
      type="button"
      class="mt-4 text-body text-fg hover:underline"
      onclick={() => (step = 'tips')}
    >
      Back
    </button>
  {:else}
    <h2 class="text-title font-semibold">Reading your footage</h2>
    <p class="mt-3 text-body text-dim">
      This list updates on its own — your session will appear here in a
      moment, then ask for a quick court setup.
    </p>
    <!-- Ingest can fail (an unreadable file quarantines to _inbox/failed and
         no session ever appears), so the waiting copy names the one place
         that failure surfaces instead of promising forever. -->
    <p class="mt-2 text-caption text-faint">
      Taking longer than a minute? Check the jobs badge in the top-right
      corner — a file the app couldn't read lands there.
    </p>
  {/if}

  {#if error}
    <div class="mt-4 text-left">
      <ErrorNote {error} subject="upload" />
    </div>
  {/if}
</section>
