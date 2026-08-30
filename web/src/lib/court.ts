/**
 * A doubles tennis court in perspective, as SVG path data.
 *
 * The camera is the one the app's own filming tips ask for: mounted on the
 * fence behind the baseline, up high, tilted down the court. Fixing it here
 * rather than parameterising it is deliberate -- the ground is meant to read
 * as *the* view SplitStep is built around, and a knob would invite drifting
 * off it.
 *
 * Pure and synchronous: CourtGround calls this once per resize, never per
 * frame.
 */

const CAMERA_HEIGHT_FT = 14
const CAMERA_Z_FT = -18 // behind the near baseline
const PITCH_RAD = (9 * Math.PI) / 180 // tilted down the court
const FOCAL = 900

// ITF dimensions, in feet.
const DOUBLES_HALF_W = 18
const SINGLES_HALF_W = 13.5
const LENGTH = 78
const NET_Z = 39
const SERVICE_OFFSET = 21
const NET_HALF_W = 21
const NET_HEIGHT_FT = 3.5

// Not ITF, unlike everything above: the real centre mark is 4 inches long,
// not 18. This stylised backdrop draws it longer so it actually reads at
// the sizes this ground renders at -- fine for a background, but it would
// be a wrong measurement anywhere this file's numbers were taken as real
// court dimensions, so it does not get to hide inside the ITF block.
const CENTRE_MARK_FT = 1.5

const RUNOFF_BACK_FT = 102
const RUNOFF_FWD_FT = -12

/** World (x across, z down the court) to unscaled screen coordinates. */
export function project(x: number, z: number): [number, number] {
  const yc = -CAMERA_HEIGHT_FT
  const zc = z - CAMERA_Z_FT
  const y2 = yc * Math.cos(PITCH_RAD) + zc * Math.sin(PITCH_RAD)
  const z2 = -yc * Math.sin(PITCH_RAD) + zc * Math.cos(PITCH_RAD)
  return [(FOCAL * x) / z2, (-FOCAL * y2) / z2]
}

export interface CourtOptions {
  width: number
  height: number
  /** Court width as a fraction of the viewport. */
  fill?: number
  /** Where the near baseline sits, as a fraction of the height. */
  baseline?: number
  /** How far the apron reaches sideways, in feet. */
  runoffWidthFt?: number
}

export interface CourtGeometry {
  runoff: string
  surface: string
  lines: string[]
  net: string
  posts: string[]
}

/**
 * A straight line in the world is still straight after this projection, but
 * subdividing costs nothing and keeps the paths robust if the camera ever
 * gains lens distortion.
 */
function segment(
  a: [number, number],
  b: [number, number],
  n = 24,
): Array<[number, number]> {
  const out: Array<[number, number]> = []
  for (let i = 0; i <= n; i++) {
    out.push(project(a[0] + ((b[0] - a[0]) * i) / n, a[1] + ((b[1] - a[1]) * i) / n))
  }
  return out
}

function ring(
  x0: number,
  z0: number,
  x1: number,
  z1: number,
): Array<[number, number]> {
  return [
    ...segment([x0, z0], [x1, z0]),
    ...segment([x1, z0], [x1, z1]),
    ...segment([x1, z1], [x0, z1]),
    ...segment([x0, z1], [x0, z0]),
  ]
}

export function courtPaths(opts: CourtOptions): CourtGeometry {
  const { width, height, fill = 0.8, baseline = 1.06, runoffWidthFt = 150 } = opts

  // Framed on the court, not on the apron: the apron's near edge is a few
  // feet from the lens and would otherwise fill the entire frame.
  const corners = [
    project(-DOUBLES_HALF_W, 0),
    project(DOUBLES_HALF_W, 0),
    project(-DOUBLES_HALF_W, LENGTH),
    project(DOUBLES_HALF_W, LENGTH),
  ]
  const xs = corners.map((p) => p[0])
  const ys = corners.map((p) => p[1])
  const scale = (width * fill) / (Math.max(...xs) - Math.min(...xs))
  const ox = width / 2 - ((Math.min(...xs) + Math.max(...xs)) / 2) * scale
  const oy = height * baseline - Math.max(...ys) * scale

  const d = (pts: Array<[number, number]>, close = false): string =>
    'M' +
    pts.map(([x, y]) => `${(x * scale + ox).toFixed(1)} ${(y * scale + oy).toFixed(1)}`).join(' L') +
    (close ? ' Z' : '')

  const s = SINGLES_HALF_W
  const dw = DOUBLES_HALF_W

  return {
    runoff: d(ring(-runoffWidthFt, RUNOFF_FWD_FT, runoffWidthFt, RUNOFF_BACK_FT), true),
    surface: d(ring(-dw, 0, dw, LENGTH), true),
    lines: [
      d(segment([-dw, 0], [dw, 0])), // near baseline
      d(segment([-dw, LENGTH], [dw, LENGTH])), // far baseline
      d(segment([-dw, 0], [-dw, LENGTH])), // doubles sidelines
      d(segment([dw, 0], [dw, LENGTH])),
      d(segment([-s, 0], [-s, LENGTH])), // singles sidelines
      d(segment([s, 0], [s, LENGTH])),
      d(segment([-s, NET_Z - SERVICE_OFFSET], [s, NET_Z - SERVICE_OFFSET])),
      d(segment([-s, NET_Z + SERVICE_OFFSET], [s, NET_Z + SERVICE_OFFSET])),
      d(segment([0, NET_Z - SERVICE_OFFSET], [0, NET_Z + SERVICE_OFFSET])),
      d(segment([0, 0], [0, CENTRE_MARK_FT])), // centre marks
      d(segment([0, LENGTH - CENTRE_MARK_FT], [0, LENGTH])),
    ],
    net: d(segment([-NET_HALF_W, NET_Z], [NET_HALF_W, NET_Z])),
    posts: [-NET_HALF_W, NET_HALF_W].map((x) => {
      const [bx, by] = project(x, NET_Z)
      const top = by - (NET_HEIGHT_FT * FOCAL) / ((NET_Z - CAMERA_Z_FT) * Math.cos(PITCH_RAD))
      const px = (bx * scale + ox).toFixed(1)
      return `M${px} ${(by * scale + oy).toFixed(1)} L${px} ${(top * scale + oy).toFixed(1)}`
    }),
  }
}
