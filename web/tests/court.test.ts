import { describe, expect, it } from 'vitest'
import { courtPaths, project } from '../src/lib/court'

/** Pull every "x y" pair out of an SVG path's d attribute. */
function points(d: string): Array<[number, number]> {
  return [...d.matchAll(/(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g)].map(
    (m) => [Number(m[1]), Number(m[2])] as [number, number],
  )
}

describe('the court projection', () => {
  it('puts the far baseline above the near one', () => {
    // Screen y grows downward, so "further away" must mean a smaller y.
    expect(project(0, 78)[1]).toBeLessThan(project(0, 0)[1])
  })

  it('narrows with distance', () => {
    const near = project(18, 0)[0] - project(-18, 0)[0]
    const far = project(18, 78)[0] - project(-18, 78)[0]
    expect(far).toBeLessThan(near)
    expect(far).toBeGreaterThan(0)
  })

  it('is symmetric about the centre line', () => {
    for (const z of [0, 21, 39, 60, 78]) {
      expect(project(-13.5, z)[0]).toBeCloseTo(-project(13.5, z)[0], 6)
      expect(project(-13.5, z)[1]).toBeCloseTo(project(13.5, z)[1], 6)
    }
  })
})

describe('courtPaths', () => {
  const geo = courtPaths({ width: 1200, height: 520 })

  it('draws every marking a doubles court has', () => {
    // 2 baselines, 2 doubles sidelines, 2 singles sidelines, 2 service
    // lines, 1 centre service line, 2 centre marks.
    expect(geo.lines).toHaveLength(11)
    expect(geo.posts).toHaveLength(2)
  })

  it('closes the surface and the run-off', () => {
    expect(geo.surface.endsWith(' Z')).toBe(true)
    expect(geo.runoff.endsWith(' Z')).toBe(true)
  })

  it('centres the court horizontally', () => {
    const xs = points(geo.surface).map((p) => p[0])
    const mid = (Math.min(...xs) + Math.max(...xs)) / 2
    expect(mid).toBeCloseTo(600, 0)
  })

  // The run-off has to reach past the frame at every aspect ratio, or an
  // ultrawide viewport shows a void beside the court where the ground
  // simply stops.
  it.each([
    [1200, 520],
    [3440, 1000],
    [1024, 1400],
  ])('covers %ix%i with run-off', (width, height) => {
    const g = courtPaths({ width, height, runoffWidthFt: 150 })
    const xs = points(g.runoff).map((p) => p[0])
    expect(Math.min(...xs)).toBeLessThan(0)
    expect(Math.max(...xs)).toBeGreaterThan(width)
  })

  it('emits no NaN', () => {
    const all = [geo.runoff, geo.surface, geo.net, ...[...geo.lines, ...geo.posts].map((p) => p.d)]
    expect(all.some((d) => d.includes('NaN'))).toBe(false)
  })
})

// CourtGround keys its {#each} blocks on these names. It used to key on the
// path string itself, and at a zero-size viewport -- a hidden window, which
// is how the Tauri shell autoboots -- every point collapses onto one pixel,
// eleven markings serialise to the same `d`, and Svelte's
// each_key_duplicate aborted the whole app before the Library route asked
// for a single session.
describe('courtPaths keys', () => {
  it.each([
    [0, 0],
    [1, 1],
    [1200, 520],
  ])('are unique at %ix%i', (width, height) => {
    const g = courtPaths({ width, height })
    for (const group of [g.lines, g.posts]) {
      const names = group.map((p) => p.name)
      expect(names.every((n) => typeof n === 'string' && n.length > 0)).toBe(true)
      expect(new Set(names).size).toBe(names.length)
    }
  })
})
