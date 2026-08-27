import { describe, expect, it } from 'vitest'
import { formatBytes } from '../src/lib/size'

describe('formatBytes', () => {
  it('formats at the right unit with one decimal under ten of it', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(412 * 1024 ** 2)).toBe('412 MB')
    expect(formatBytes(1.44 * 1024 ** 3)).toBe('1.4 GB')
    expect(formatBytes(2.04 * 1024 ** 4)).toBe('2.0 TB')
  })

  it('never shows a decimal above ten of a unit', () => {
    expect(formatBytes(64.2 * 1024 ** 3)).toBe('64 GB')
  })
})
