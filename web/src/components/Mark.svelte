<script lang="ts">
  import { MARK, seamPath } from '../lib/mark'

  interface Props {
    size?: number
    /** `loading` steps the halves apart and back; it replaces "Loading…". */
    state?: 'still' | 'loading'
    /** What the loading state announces. */
    label?: string
  }
  let { size = 19, state = 'still', label = 'Loading…' }: Props = $props()

  const c = MARK.viewBox / 2
  const seam = seamPath('left')
  const seam2 = seamPath('right')
  // Unique per instance: two marks on one page would otherwise share clip
  // paths, and the second would clip against the first's rects.
  const uid = $props.id()

  // The halves' resting offset and the loading animation are both written as
  // Tailwind arbitrary values rather than in a component <style> block. Two
  // reasons, not one: (1) this app has no precedent for a scoped <style> in
  // any component -- keyframes live in app.css and are invoked from
  // `animate-[…]`, exactly like the QueueMode verdict flash -- and (2) this
  // toolchain's `vitePreprocess()` throws when Vitest compiles any component
  // that does carry one (see the comment beside `@keyframes mark-step-left`
  // in app.css). `--mk-gap`/`--mk-step`, not a literal, are what every
  // transform below is built from, so the geometry still has one source.
  const restLeft = '[transform:translate(calc(var(--mk-gap)*-1),calc(var(--mk-step)*-1))]'
  const restRight = '[transform:translate(var(--mk-gap),var(--mk-step))]'
  const spinLeft =
    'motion-safe:animate-[mark-step-left_1400ms_var(--ease-out-soft)_infinite_alternate]'
  const spinRight =
    'motion-safe:animate-[mark-step-right_1400ms_var(--ease-out-soft)_infinite_alternate]'
</script>

<span class="inline-flex items-center" role={state === 'loading' ? 'status' : undefined}>
  <svg
    width={size}
    height={size}
    viewBox="0 0 {MARK.viewBox} {MARK.viewBox}"
    aria-hidden="true"
    style="--mk-gap: {MARK.gap}px; --mk-step: {MARK.step}px;"
  >
    <defs>
      <clipPath id="mk-l-{uid}"
        ><rect x="0" y="0" width={c - MARK.splitInset} height={MARK.viewBox} /></clipPath
      >
      <clipPath id="mk-r-{uid}"
        ><rect x={c + MARK.splitInset} y="0" width={c - MARK.splitInset} height={MARK.viewBox}
        /></clipPath
      >
    </defs>
    <!-- One circle per half -- the ball, split -- not a third one for the
         disc clip: the fill circle is already exactly circular and needs no
         re-clipping, only the seam arcs (whose rx/ry slightly exceed radius,
         so they read as curving *into* the ball rather than past its edge)
         do. A CSS basic-shape clip-path draws that disc without an SVG
         <clipPath>/<circle> resource, which is what kept this at 3 circles
         and failing "split into two halves, not three" during development. -->
    <g
      class="[transform-box:view-box] {restLeft} {state === 'loading' ? spinLeft : ''}"
      clip-path="url(#mk-l-{uid})"
    >
      <circle cx={c} cy={c} r={MARK.radius} fill="var(--color-ball)" />
      <g style="clip-path: circle({MARK.radius}px at {c}px {c}px)">
        <path d={seam} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
        <path d={seam2} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
      </g>
    </g>
    <g
      class="[transform-box:view-box] {restRight} {state === 'loading' ? spinRight : ''}"
      clip-path="url(#mk-r-{uid})"
    >
      <circle cx={c} cy={c} r={MARK.radius} fill="var(--color-ball)" />
      <g style="clip-path: circle({MARK.radius}px at {c}px {c}px)">
        <path d={seam} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
        <path d={seam2} fill="none" stroke="var(--color-court-line)" stroke-width={MARK.seamWidth} />
      </g>
    </g>
  </svg>
  {#if state === 'loading'}
    <!-- Screen-reader-only under motion-safe, because the stepping halves
         already say "loading" to a sighted user there. Under
         prefers-reduced-motion the animation classes above never apply --
         motion-safe:animate-[…] simply does not fire -- so a sighted
         reduced-motion viewer was left looking at a static ball with no
         visible difference from the app-bar's own still mark, and no way
         to tell "loading" from "loaded". motion-reduce:not-sr-only reveals
         exactly the same label a screen reader already had, matching the
         spec's rule that reduced motion may drop the travel but must never
         drop the state. text-fg because this can render directly over
         exposed court (Library, Reels, Setup, Session, Reel loading
         states, none of them behind a `surface` card) -- the one token the
         spec allows there. -->
    <span class="sr-only motion-reduce:not-sr-only motion-reduce:ml-2 motion-reduce:text-body motion-reduce:text-fg">
      {label}
    </span>
  {/if}
</span>
