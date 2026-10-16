<script lang="ts">
  import { FORMATS, MOUNTS, untunedNote } from '../lib/mount'
  import type { Mount, PlayFormat } from '../lib/types'

  // The two questions that pick a detector, as two rows of buttons. Shared
  // by the setup wizard and the session page's corners editor so the two
  // places a reviewer answers them cannot drift apart in wording or order.
  // The caller owns the values and decides when they are written: the
  // wizard sends them with setup, the session page with /choices.
  interface Props {
    mount: Mount
    format: PlayFormat
    onmount: (m: Mount) => void
    onformat: (f: PlayFormat) => void
    disabled?: boolean
  }

  let { mount, format, onmount, onformat, disabled = false }: Props = $props()

  // Keyed on the answers, never on the server's `scorer_now`: the note is
  // about the choice on screen, which is not saved yet in the wizard.
  const note = $derived(untunedNote(mount, format))
</script>

<!-- Selected is `border-fg` and weight, unselected `border-line`: the app
     has no accent colour (CLAUDE.md, Design tokens). `aria-pressed` carries
     the same state for a screen reader. -->
{#snippet choice(label: string, selected: boolean, onclick: () => void, group: string)}
  <button
    class="rounded border px-2 py-0.5 text-caption hover:bg-surface-2 disabled:opacity-40
           motion-safe:transition-colors {selected
      ? 'border-fg font-semibold text-fg'
      : 'border-line text-dim'}"
    aria-pressed={selected}
    aria-label="{group} {label}"
    {onclick}
    {disabled}
  >
    {label}
  </button>
{/snippet}

<div class="space-y-2">
  <div class="flex flex-wrap items-center gap-2" role="group" aria-label="camera mount">
    <span class="w-14 text-caption text-dim">Camera</span>
    {#each MOUNTS as m (m.value)}
      {@render choice(m.label, mount === m.value, () => onmount(m.value), 'mount')}
    {/each}
  </div>
  <div class="flex flex-wrap items-center gap-2" role="group" aria-label="play format">
    <span class="w-14 text-caption text-dim">Play</span>
    {#each FORMATS as f (f.value)}
      {@render choice(f.label, format === f.value, () => onformat(f.value), 'format')}
    {/each}
  </div>
  {#if note}
    <p data-role="untuned-note" class="text-caption text-dim">{note}</p>
  {/if}
</div>
