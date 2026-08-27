/**
 * Bytes → a short human size for the library header: `412 MB`, `1.4 GB`.
 *
 * One decimal below ten of a unit, none above: the digit is meaningful at
 * `1.4 GB` and noise at `64.2 GB`, and the header wants the shortest string
 * that still moves when the library grows. Binary units, matching what
 * Finder's byte counts came from.
 */
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

export function formatBytes(n: number): string {
  let value = Math.max(0, n)
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  const text = value < 10 && unit > 0 ? value.toFixed(1) : String(Math.round(value))
  return `${text} ${UNITS[unit]}`
}
