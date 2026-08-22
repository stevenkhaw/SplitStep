import { clamp } from './time'

/**
 * `list` with the item at `from` moved to `to`.
 *
 * Pure and non-mutating, like every other module under lib/: the component
 * owns only `getBoundingClientRect`, and everything that decides where a row
 * lands is testable without a DOM. Drag-to-reorder is hand-rolled here for
 * the same reason ZoomBand and QuadEditor are -- a drag library would be a
 * dependency for arithmetic this small.
 *
 * `to` is clamped rather than validated: a pointer dragged past the end of
 * the list is an ordinary gesture, and losing the row over it is the worst
 * available answer. An out-of-range `from`, by contrast, is a caller bug
 * with no sensible interpretation, so it returns the list unchanged.
 */
export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (from < 0 || from >= list.length) return [...list]
  if (from === to) return [...list]
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(clamp(to, 0, next.length), 0, item)
  return next
}

/**
 * The index `moveItem` should be given for a drag currently at `pointerY`.
 *
 * `midpoints` are the vertical centres of every row IN LIST ORDER, in the
 * same coordinate space as `pointerY` (viewport pixels, straight off
 * getBoundingClientRect). Midpoints rather than edges: a row swaps when the
 * pointer passes the middle of its neighbour, which is what makes the
 * gesture feel like the row is displacing the one it crosses.
 *
 * The dragged row's own midpoint is excluded before counting, which is what
 * makes the result directly usable as `moveItem(list, from, dropIndex(...))`
 * -- moveItem removes the item before splicing it back, so both functions
 * are reasoning about the same shortened list. Doing this any other way
 * means off-by-one arithmetic in the component, which is exactly what this
 * module exists to keep out of there.
 */
export function dropIndex(pointerY: number, midpoints: number[], fromIndex: number): number {
  const rest = midpoints.filter((_, i) => i !== fromIndex)
  let index = 0
  while (index < rest.length && pointerY > rest[index]) index++
  return index
}
