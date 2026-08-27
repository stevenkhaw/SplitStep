<script lang="ts">
  import { appmode } from '../lib/appmode.svelte'
  import { primaryShortcuts, shortcutGroups, isHelpKey } from '../lib/shortcuts'
  import type { ShortcutMode } from '../lib/shortcuts'

  interface Props {
    mode: ShortcutMode
  }
  let { mode }: Props = $props()

  let open = $state(false)
  let closeButton = $state<HTMLButtonElement>()
  let restoreTo: HTMLElement | null = null

  // Capture phase, and deliberately not `<svelte:window onkeydown>`.
  //
  // Escape is also TimelineMode's and LabelMode's "leave this mode" key, and
  // those handlers are bubble-phase listeners on `window`. An Escape meant to
  // close this overlay would therefore also kick the reviewer out of the
  // timeline. A capture-phase listener on `window` runs before every
  // bubble-phase one regardless of which component mounted first, so
  // stopImmediatePropagation here is reliable rather than a bet on
  // registration order -- which is exactly the ordering dependence the jobs
  // badge declined to take on.
  $effect(() => {
    function onKey(e: KeyboardEvent) {
      if (open && e.key === 'Escape') {
        e.preventDefault()
        e.stopImmediatePropagation()
        open = false
        return
      }
      if (!open && isHelpKey(e)) {
        e.preventDefault()
        e.stopImmediatePropagation()
        restoreTo = document.activeElement instanceof HTMLElement ? document.activeElement : null
        open = true
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  // Focus moves into the dialog on open and back where it came from on close,
  // so the overlay is reachable and escapable without a mouse.
  $effect(() => {
    if (open) closeButton?.focus()
    else restoreTo?.focus()
  })

  const groups = $derived(shortcutGroups(mode, appmode.current))
  const primary = $derived(primaryShortcuts(mode, appmode.current))
  // A fixed minimum width and centred glyph, so every key reads as the same
  // object. Without it the boxes size to their content and the two narrow
  // ones -- `,` and `.` -- render as a low, off-centre speck in a box wide
  // enough to look empty.
  const KEY =
    'inline-block min-w-6 rounded border border-line bg-surface-2 px-1.5 text-center' +
    ' font-data text-caption text-dim'
</script>

<p class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-faint">
  {#each primary as s (s.label)}
    <span class="whitespace-nowrap">
      {#each s.keys as k (k)}<kbd class={KEY}>{k}</kbd>{' '}{/each}
      {s.label}
    </span>
  {/each}
</p>

{#if open}
  <!-- The backdrop closes on click. It tests `target === currentTarget`
       rather than letting the panel stop propagation, so the panel needs no
       click handler of its own -- a non-interactive element carrying one is
       what `a11y_click_events_have_key_events` objects to, and there is no
       keyboard gesture to pair with "click the backdrop" anyway. Escape is
       the keyboard equivalent and is handled above.

       A plain div rather than <dialog> because showModal() moves the node to
       the top layer and out of the component's own stacking context, which
       fights the app's z-indexed toasts for no gain here. -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
    role="presentation"
    onclick={(e) => {
      if (e.target === e.currentTarget) open = false
    }}
  >
    <div
      class="max-h-full w-full max-w-2xl overflow-y-auto rounded-xl border border-line bg-surface
             p-6 shadow-2xl"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      tabindex="-1"
    >
      <div class="mb-5 flex items-baseline justify-between gap-4">
        <h2 class="text-title font-semibold">Keyboard shortcuts</h2>
        <button
          bind:this={closeButton}
          type="button"
          class="rounded border border-line px-2 py-1 font-data text-caption text-dim
                 hover:bg-surface-2 motion-safe:transition-colors"
          onclick={() => (open = false)}
        >
          Esc
        </button>
      </div>

      <div class="grid gap-x-8 gap-y-5 sm:grid-cols-2">
        {#each groups as g (g.title)}
          <section>
            <h3 class="mb-2 font-data text-caption tracking-wide text-faint uppercase">
              {g.title}
            </h3>
            <dl class="space-y-1.5">
              {#each g.items as s (s.label)}
                <div class="flex items-baseline justify-between gap-3">
                  <dt class="text-caption text-dim">{s.label}</dt>
                  <dd class="flex shrink-0 gap-1">
                    {#each s.keys as k (k)}<kbd class={KEY}>{k}</kbd>{/each}
                  </dd>
                </div>
              {/each}
            </dl>
          </section>
        {/each}
      </div>
    </div>
  </div>
{/if}
