<script lang="ts">
  import {
    chooserCopy,
    chooserState,
    defaultLibraryPath,
    describeTarget,
    sortEntries,
    type LibraryEntry,
  } from '../lib/launcher'
  import { resolveBridge } from './bridge'

  const bridge = resolveBridge()

  let known = $state<LibraryEntry[]>([])
  let configured = $state<string | null>(null)
  let target = $state('')
  let free = $state<number | null>(null)
  let targetHasLibrary = $state(false)
  let busy = $state(false)
  let error = $state<string | null>(null)
  let loaded = $state(false)

  const chooser = $derived(chooserState(known, configured))
  const copy = $derived(chooserCopy(chooser, configured))
  const entries = $derived(sortEntries(known))

  $effect(() => {
    void (async () => {
      const [home, list, current] = await Promise.all([
        bridge.home(),
        bridge.known(),
        bridge.configured(),
      ])
      known = list
      configured = current
      if (!target) await setTarget(defaultLibraryPath(home))
      loaded = true
    })()
  })

  async function setTarget(path: string) {
    target = path
    // Both are cheap native calls; running them together keeps the
    // description from flickering through a half-updated state.
    const [space, existing] = await Promise.all([
      bridge.freeSpace(path),
      bridge.hasLibrary(path),
    ])
    free = space
    targetHasLibrary = existing
  }

  async function choose() {
    const picked = await bridge.pickFolder()
    if (picked) await setTarget(picked)
  }

  async function go(path: string, create: boolean) {
    busy = true
    error = null
    try {
      await bridge.open(path, create)
      // No success branch: open() navigates the window away on success, so
      // reaching the next line at all means something is wrong.
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
      busy = false
    }
  }
</script>

<main class="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 p-8">
  <header class="flex flex-col gap-2">
    <h1 class="text-display text-fg">{copy.heading}</h1>
    <p class="text-body text-dim">{copy.body}</p>
  </header>

  {#if loaded && entries.length > 0}
    <section class="flex flex-col gap-2">
      <h2 class="text-caption text-faint">Libraries you have opened</h2>
      <ul class="flex flex-col gap-2">
        {#each entries as item (item.path)}
          <li>
            <button
              class="flex w-full flex-col items-start gap-0.5 rounded border border-line
                     bg-surface p-3 text-left hover:bg-surface-2
                     disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!item.reachable || busy}
              onclick={() => go(item.path, false)}
            >
              <span class="text-body text-fg">{item.path.split('/').pop()}</span>
              <span class="text-caption font-data break-all text-faint">{item.path}</span>
              {#if !item.reachable}
                <span class="text-caption text-danger">Not connected</span>
              {/if}
            </button>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <section class="flex flex-col gap-2 rounded border border-line bg-surface p-3">
    <h2 class="text-caption text-faint">
      {targetHasLibrary ? 'This folder' : 'New library'}
    </h2>
    <span class="text-caption font-data break-all text-dim">{target}</span>
    <span class="text-caption text-faint">{describeTarget(free, targetHasLibrary)}</span>
    <div class="flex items-center gap-3 pt-1">
      <button
        class="rounded bg-accent px-3 py-1.5 text-body text-bg disabled:opacity-50"
        disabled={busy || !target}
        onclick={() => go(target, !targetHasLibrary)}
      >
        {busy ? 'Starting…' : targetHasLibrary ? 'Open' : 'Create'}
      </button>
      <button class="text-body text-accent disabled:opacity-50" disabled={busy} onclick={choose}>
        Choose folder…
      </button>
    </div>
  </section>

  {#if error}
    <p class="text-caption text-danger">{error}</p>
  {/if}
</main>
