/**
 * Turning a thrown request failure into something worth reading.
 *
 * The error banners rendered the thrown string verbatim:
 *
 *     GET /api/sessions/nope -> 404 {"detail":"Session not found"}
 *
 * Method, path, status code and a JSON envelope are implementation detail.
 * They are demoted here rather than discarded, though, and that is a
 * deliberate difference from how a hosted app would handle this: the person
 * reading the banner is also the person running `splitstep serve`, so the
 * technical line is the second thing they want, not something to hide.
 */
export interface ApiErrorInfo {
  status: number
  method: string
  path: string
  /** The server's own message, unwrapped from FastAPI's {"detail": …}. */
  detail: string
}

/** Still an Error, so every existing `catch` and `String(e)` keeps working;
 *  the structured fields are additive. */
export class ApiError extends Error {
  readonly info: ApiErrorInfo

  constructor(message: string, info: ApiErrorInfo) {
    super(message)
    this.name = 'ApiError'
    this.info = info
  }
}

export interface FriendlyError {
  /** One sentence, in the interface's voice. */
  message: string
  /** The technical line, for the banner to render quietly beneath. */
  detail: string | null
}

function technical(info: ApiErrorInfo): string {
  return [`${info.method} ${info.path} → ${info.status}`, info.detail]
    .filter(Boolean)
    .join(' ')
}

/**
 * `subject` is what the caller is currently showing -- 'session', 'reel',
 * 'source'. It is a parameter because the failure carries no idea what the
 * user was looking at, and "Not found" without a noun is the same vagueness
 * the raw string had.
 */
export function describeApiError(e: unknown, subject: string): FriendlyError {
  // fetch() rejects with a TypeError and no status when it cannot reach the
  // host at all. For a local app on an external drive this is the *likely*
  // failure -- the server was stopped, or the drive was ejected under it --
  // and "Failed to fetch" names nothing the user can act on.
  if (e instanceof TypeError) {
    return {
      message: 'Can’t reach the server. Check that splitstep serve is still running.',
      detail: e.message,
    }
  }

  if (e instanceof ApiError) {
    const { status, detail } = e.info
    if (status === 404) {
      return {
        message: `That ${subject} is not in the library any more.`,
        detail: technical(e.info),
      }
    }
    if (status >= 500) {
      return {
        message: 'The server hit an error. Check the terminal running splitstep serve.',
        detail: technical(e.info),
      }
    }
    // Any other 4xx is the server rejecting something specific -- a rotation
    // that is not a right angle, a quad outside the frame. That message is
    // already written for a person and is better than anything this function
    // could substitute for it.
    if (detail) return { message: detail, detail: technical(e.info) }
  }

  return {
    message: 'Something went wrong.',
    detail: e instanceof Error ? e.message : String(e),
  }
}
