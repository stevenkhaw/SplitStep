<script lang="ts">
  import { courtPaths } from '../lib/court'

  interface Props {
    /** browse: full strength. review: frozen, dropped back, scrimmed hard. */
    tier: 'browse' | 'review'
  }
  let { tier }: Props = $props()

  // Fixed to the viewport rather than to the page, so a long timeline does
  // not scroll the court away and leave bare bg behind the content.
  let w = $state(1600)
  let h = $state(900)

  const geo = $derived(courtPaths({ width: w, height: h }))

  // The scrim is not decoration: `dim` measures 2.41:1 and `faint` 2.09:1
  // over bare court, so the ground has to be darkened wherever the layout
  // might put secondary text over it. Browse keeps the middle band open
  // because cards carry their own `surface`; review darkens throughout.
  const scrim = $derived(
    tier === 'review'
      ? { top: 0.92, mid: 0.74, bottom: 0.9 }
      : { top: 0.82, mid: 0.18, bottom: 0.62 },
  )
</script>

<svelte:window bind:innerWidth={w} bind:innerHeight={h} />

<div
  class="pointer-events-none fixed inset-0 -z-10 motion-safe:transition-opacity motion-safe:duration-slow"
  style="opacity: {tier === 'review' ? 0.55 : 1}"
>
  <!-- aria-hidden: this is wallpaper. It carries no information a reader
       needs and announcing sixteen paths would be noise. -->
  <svg
    class="h-full w-full"
    viewBox="0 0 {w} {h}"
    preserveAspectRatio="xMidYMax slice"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="court-scrim" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--color-bg)" stop-opacity={scrim.top} />
        <stop offset="45%" stop-color="var(--color-bg)" stop-opacity={scrim.mid} />
        <stop offset="100%" stop-color="var(--color-bg)" stop-opacity={scrim.bottom} />
      </linearGradient>
    </defs>
    <path d={geo.runoff} fill="var(--color-court-run)" />
    <path d={geo.surface} fill="var(--color-court)" />
    {#each geo.lines as line (line)}
      <path
        d={line}
        fill="none"
        stroke="var(--color-court-line)"
        stroke-opacity="0.7"
        stroke-width="2"
        stroke-linecap="square"
      />
    {/each}
    <path d={geo.net} fill="none" stroke="var(--color-court-line)" stroke-opacity="0.45" stroke-width="2" />
    {#each geo.posts as post (post)}
      <path d={post} fill="none" stroke="var(--color-court-line)" stroke-opacity="0.5" stroke-width="3" />
    {/each}
    <rect width={w} height={h} fill="url(#court-scrim)" />
  </svg>
</div>
