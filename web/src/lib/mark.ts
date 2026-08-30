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
  seamWidth: 2.6,
  seamRx: 13.8,
  seamRy: 13.3,
  seamTopY: 4.5,
  seamBottomY: 27.5,
  seamLeftX: 6.8,
  seamRightX: 25.2,
} as const
