/**
 * The SplitStep mark: a tennis ball cut in two, the halves stepped apart.
 * The product's name, drawn.
 *
 * These numbers are the single source of truth for the shape. The mark is
 * drawn twice -- as SVG here for the app, and in Pillow by
 * packaging/make_icon.py for the .icns -- and two drawings of one shape
 * drift silently until the Dock icon and the header are different logos.
 * tests/test_icon.py parses this file and asserts the Python side matches.
 *
 * All values are in the 32-unit viewBox.
 */
export const MARK = {
  viewBox: 32,
  radius: 13,
  /** Horizontal half-gap between the two halves. */
  gap: 0.9,
  /** Vertical offset of each half: the "step". */
  step: 1.5,
  /**
   * How far short of dead centre each half's clip window stops, on top of
   * the ±gap resting translate -- so the *visible* split is 2*(splitInset +
   * gap), not 2*gap alone. This used to be a literal `0.5` written only into
   * Mark.svelte's clip rects, invisible to packaging/make_icon.py, whose
   * crop fell at exactly `half` with no matching inset: the SVG's split
   * came out to 2.8 units and the icon's to ~1.8, two different logos at
   * two different widths. Both files now read this one number.
   */
  splitInset: 0.5,
  seamWidth: 2.6,
  seamRx: 13.8,
  seamRy: 13.3,
  seamTopY: 4.5,
  seamBottomY: 27.5,
  seamLeftX: 6.8,
  seamRightX: 25.2,
} as const

/**
 * The seam: two arcs bulging toward the centre -- the detail that reads a
 * plain circle as a tennis ball. Built here, not inline in Mark.svelte's
 * `<script>`, because this repo's rule is that logic lives in `lib/`: these
 * `d` strings are the actual geometry (packaging/make_icon.py's
 * `seam_arc()` is a from-scratch re-derivation of exactly the curve they
 * draw -- see the comment there), so keeping them out of the component is
 * what makes "one file describes the mark" true of the seam too, not just
 * of the numbers underneath it.
 */
export function seamPath(side: 'left' | 'right'): string {
  const x = side === 'left' ? MARK.seamLeftX : MARK.seamRightX
  const sweep = side === 'left' ? 1 : 0
  return `M${x} ${MARK.seamTopY} A${MARK.seamRx} ${MARK.seamRy} 0 0 ${sweep} ${x} ${MARK.seamBottomY}`
}
