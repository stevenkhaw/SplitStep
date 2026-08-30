<script lang="ts">
  import Credits from './Credits.svelte'
  import JobsBadge from './JobsBadge.svelte'
  import Mark from './Mark.svelte'
  import { appmode } from '../lib/appmode.svelte'
  import { librarySize } from '../lib/librarysize.svelte'
  import { formatBytes } from '../lib/reels'
  import { createRouter, navigate } from '../lib/router.svelte'
  import type { Route } from '../lib/router.svelte'
  import { changeLibrary, inShell } from '../lib/shell'

  const router = createRouter()
  let settingsOpen = $state(false)
  let switchError = $state<string | null>(null)

  // Refreshed whenever Sessions comes back into view, not once at mount.
  // Before the bar existed this call sat at the top of Library.svelte's
  // script and so ran on every remount of that route; a bar that mounts
  // once would otherwise leave the figure frozen for a whole session, and
  // exporting clips -- which is what actually consumes the drive -- happens
  // inside one. The store is stale-while-revalidate, so the old figure stays
  // on screen while the new one loads and nothing blanks.
  $effect(() => {
    if (router.current.name === 'library') void librarySize.refresh()
  })

  async function switchLibrary() {
    switchError = null
    try {
      await changeLibrary()
      // No success branch: the shell navigates the window away to the
      // chooser, so reaching the next line means it did not.
    } catch (e) {
      switchError = e instanceof Error ? e.message : String(e)
    }
  }

  // Two tabs, and the route decides which is lit. `reel` counts as Reels:
  // a reel's own page is inside that section, not a third place.
  //
  // Typed explicitly rather than `as const`: two tabs each carrying a
  // different literal tuple for `matches` makes TABS a union of two array
  // shapes, and `.includes()` on that union collapses its parameter to
  // `never` -- a real strict-mode failure, not a style choice.
  const TABS: { label: string; to: string; matches: readonly Route['name'][] }[] = [
    { label: 'Sessions', to: '/', matches: ['library', 'session', 'setup'] },
    { label: 'Reels', to: '/reels', matches: ['reels', 'reel'] },
  ]
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape' && settingsOpen) settingsOpen = false
  }}
/>

<header
  class="sticky top-0 z-30 flex h-13 items-center gap-4 border-b border-line px-5"
>
  <!-- The translucent blur lives on this layer, not on the header itself.
       An element with `backdrop-filter` becomes the containing block for
       its `position: fixed` descendants (CSS Filter Effects Module) -- and
       the Settings popover's click-catching backdrop further down is one
       of those. With the filter on `header`, that backdrop was clipped to
       the bar's own 52px row instead of covering the viewport, so clicking
       anywhere on the actual page stopped closing the popover.

       No `relative` added to `header` for this: it is already `position:
       sticky`, which the spec already recognises as positioned and
       therefore already a valid containing block for this `absolute`
       child -- adding `relative` on top would fight the very `sticky` this
       header depends on to stay pinned while the page scrolls.

       `-z-10` looks like it should sink this behind CourtGround's own
       `fixed inset-0 -z-10` wallpaper, but it does not: `header` already
       has an explicit `z-30` (plus its `sticky` position), which makes it
       a stacking context of its own. A negative z-index only reorders a
       stacking context's *own* children against each other -- it cannot
       let a descendant escape into a sibling stacking context outside its
       ancestor. So this layer paints behind the bar's brand/nav/utility
       content (all default z-index:auto within this same context), while
       the header as a whole still paints at z=30 above CourtGround and
       `<main>`, exactly as before. -->
  <div class="pointer-events-none absolute inset-0 -z-10 bg-bg/70 backdrop-blur-lg"></div>

  <a href="#/" class="flex items-center gap-2">
    <Mark size={19} />
    <span class="text-body font-semibold tracking-tight">SplitStep</span>
  </a>

  <nav class="flex gap-0.5">
    {#each TABS as tab (tab.to)}
      {@const active = tab.matches.includes(router.current.name)}
      <button
        class="rounded-lg border px-3 py-1.5 text-body motion-safe:transition-colors
               motion-safe:duration-quick
               {active
          ? 'border-line bg-surface-2 text-fg'
          : 'border-transparent text-dim hover:text-fg'}"
        aria-current={active ? 'page' : undefined}
        onclick={() => navigate(tab.to)}
      >
        {tab.label}
      </button>
    {/each}
  </nav>

  <div class="ml-auto flex items-center gap-3">
    <!-- The keep-everything policy's one disk affordance: informational, no
         action attached (spec 2026-08-26; Reclaim Space is rejected, not
         deferred -- see CLAUDE.md). The number is `/api/library/stats`'s
         total bytes under the library root: what the library itself takes
         up, not what the drive has left. Label it "used", never "free" --
         this chip once read "33 GB free" on a 2TB drive where 33 GB was the
         library's own footprint, the exact opposite of free space, and
         nobody caught it before a user did. It sits with Settings rather
         than beside the nav, because it is status and not a destination --
         which is exactly what it looked like before. -->
    {#if librarySize.bytes !== null}
      <span class="rounded-full border border-line px-2.5 py-1 font-data text-caption text-faint">
        {formatBytes(librarySize.bytes)} used
      </span>
    {/if}
    <div class="relative">
      <button class="font-data text-data text-dim hover:text-fg motion-safe:transition-colors"
              aria-expanded={settingsOpen}
              onclick={() => (settingsOpen = !settingsOpen)}>Settings</button>
      {#if settingsOpen}
        <!-- Same dismissal pair the jobs panel beside this one offers:
             Escape (svelte:window below) and clicking anywhere else. The
             backdrop is transparent -- it exists to catch the outside
             click, not to dim the page for a two-line popover. -->
        <div
          class="fixed inset-0 z-10"
          role="presentation"
          onclick={() => (settingsOpen = false)}
        ></div>
        <div class="absolute right-0 z-20 mt-2 w-72 rounded-lg border border-line bg-surface p-4
                    text-left shadow-2xl">
          <label class="flex items-start gap-2 text-body">
            <input
              type="checkbox"
              class="mt-1 accent-fg"
              checked={appmode.current === 'dev'}
              onchange={(e) => appmode.set(e.currentTarget.checked ? 'dev' : 'friend')}
            />
            <span>
              Advanced tools — label mode and re-segment
              <span class="mt-1 block text-caption text-faint">
                Hidden in friend mode so a stray keypress can't write to the
                training corpus or rebuild a reviewed session.
              </span>
            </span>
          </label>
          {#if inShell()}
            <!-- Shell-only: a browser tab has nothing to ask. Separated by a
                 rule because it is a different kind of thing from the toggle
                 above -- that changes what this library shows, this leaves
                 the library entirely. -->
            <div class="mt-3 border-t border-line pt-3">
              <button class="text-body text-fg" onclick={switchLibrary}>
                Change library…
              </button>
              <span class="mt-1 block text-caption text-faint">
                Nothing is moved. This library stays where it is, and you can
                come back to it.
              </span>
              {#if switchError}
                <span class="mt-1 block text-caption text-danger">{switchError}</span>
              {/if}
            </div>
          {/if}
          <Credits />
        </div>
      {/if}
    </div>
    <JobsBadge />
  </div>
</header>
