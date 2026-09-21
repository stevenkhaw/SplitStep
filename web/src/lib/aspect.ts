/**
 * The shape of the footage, for the boxes that hold it.
 *
 * Every video box in this app was a hardcoded 16:9, which was true of every
 * source until it wasn't: six of the eleven on the drive are a phone held on
 * its end, and a 9:16 proxy in a 16:9 box is pillarboxed on screen exactly
 * the way the clips themselves used to be (see
 * splitstep/media/transcode.py::clip_frame for the render side of the same
 * complaint). Nothing here changes a pixel of the footage -- it decides how
 * much of the window the element holding it gets.
 */

/** What a box is until the footage says otherwise. */
export const DEFAULT_ASPECT = 16 / 9

/**
 * A source's display aspect as width/height, falling back to 16:9.
 *
 * `sources.width/height` is the PROXY's frame -- `set_source_dimensions`
 * probes the proxy the wizard's rotation produced -- so it is already
 * display-oriented and needs no rotation re-applied. A row that predates a
 * proxy build carries zeroes, and a `<video>` reports 0x0 before its
 * metadata arrives; both mean "not known yet", and both have to answer with
 * a usable number rather than a division by zero, because this value goes
 * straight into a CSS `aspect-ratio`.
 */
export function videoAspect(
  width: number | null | undefined,
  height: number | null | undefined,
): number {
  if (!width || !height || width <= 0 || height <= 0) return DEFAULT_ASPECT
  return width / height
}

/** Whether footage of this aspect is taller than it is wide. */
export function isPortrait(aspect: number): boolean {
  return aspect < 1
}

/**
 * The height a deck gets when its caller does not budget one itself.
 *
 * A landscape deck never needed a cap -- a 16:9 box at any sane page width
 * is shorter than the window. A 9:16 box is not: at 1440px wide it wants to
 * be 2560px tall, which pushes every control under it off the screen and
 * makes the reviewer scroll to see footage that was already playing. So the
 * budget is a height, and the width follows from it.
 */
export const DEFAULT_DECK_HEIGHT = '70vh'

/**
 * How wide a deck of this aspect may grow, given the height it may occupy.
 *
 * A width cap rather than a height one because the box's size comes from
 * `aspect-ratio` on a block element: constrain the width and the height
 * follows, and the two can never disagree about the shape. Capping the
 * height directly would need the element to also stop filling its parent's
 * width, which is two rules to keep in step instead of one.
 *
 * Returned as a `calc()` string for an inline style rather than written as
 * a Tailwind arbitrary value: the multiplier depends on the source, and
 * Tailwind's arbitrary-value syntax is resolved at build time from scanned
 * source text, so it cannot carry a number computed at runtime. The unit
 * arithmetic is still a plain multiply, which is why it lives here with a
 * test rather than inline in the component.
 */
export function deckMaxWidth(aspect: number, available: string = DEFAULT_DECK_HEIGHT): string {
  return `calc(${available} * ${aspect})`
}

/**
 * The height TimelineMode's deck may occupy.
 *
 * Capped so the deck, the status line, both bands and the threshold row
 * share one screen -- trimming is a deck-and-band gesture and scrolling
 * between them is the failure it removes. 28rem is what sits below the deck
 * plus the page header.
 */
export const TIMELINE_DECK_HEIGHT = 'calc(100vh - 28rem)'
