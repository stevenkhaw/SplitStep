<script lang="ts">
  import { scoreCurvePoints, scoreToY } from '../lib/timeline'

  interface Props {
    scores: number[]
    threshold: number
    stepMs: number
    windowStartMs: number
    windowEndMs: number
  }

  let { scores, threshold, stepMs, windowStartMs, windowEndMs }: Props = $props()

  const WIDTH = 1000
  const HEIGHT = 38

  // All the index/window -> SVG-coordinate math lives in lib/timeline.ts
  // (scoreCurvePoints, scoreToY) so it's unit-tested independent of the DOM.
  const points = $derived(
    scoreCurvePoints(scores, stepMs, windowStartMs, windowEndMs, WIDTH, HEIGHT),
  )
  const thresholdY = $derived(scoreToY(threshold, HEIGHT))
</script>

<svg
  viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
  preserveAspectRatio="none"
  class="block h-10 w-full"
  role="img"
  aria-label="detector score curve, threshold shown as a dashed line"
>
  <polyline
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    {points}
    class="text-dim"
  />
  <line
    x1="0"
    x2={WIDTH}
    y1={thresholdY}
    y2={thresholdY}
    stroke="currentColor"
    stroke-width="1.5"
    stroke-dasharray="5 4"
    class="text-fg"
  />
</svg>
