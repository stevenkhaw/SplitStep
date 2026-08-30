export type ToastTone = 'error' | 'info'

export interface Toast {
  id: number
  message: string
  tone: ToastTone
}

const DEFAULT_DURATION_MS = 4000

/**
 * A tiny reactive queue of brief, non-blocking notices -- e.g. "a persist
 * failed and was auto-reverted". Each pushed message disappears on its own
 * after `durationMs`; nothing here blocks the caller or requires
 * acknowledgement, which is the point -- queue mode must keep advancing
 * through a rare server hiccup, not stop and wait on the user noticing a
 * toast.
 */
export function createToaster(durationMs: number = DEFAULT_DURATION_MS) {
  let toasts = $state<Toast[]>([])
  let nextId = 0

  function dismiss(id: number): void {
    toasts = toasts.filter((t) => t.id !== id)
  }

  // `tone` defaults to 'error' -- not because error is a neutral default,
  // but because every caller that existed before tones did (star/reject
  // persist failing, a boundary edit getting refused) is a genuine failure,
  // and a one-argument push() must keep reading red for those without every
  // call site being touched. The one caller that wants otherwise (export's
  // success notice) passes 'info' explicitly; defaulting the other way
  // would silently restyle the failure paths into something calmer than
  // they are.
  function push(message: string, tone: ToastTone = 'error'): number {
    const id = nextId++
    toasts = [...toasts, { id, message, tone }]
    setTimeout(() => dismiss(id), durationMs)
    return id
  }

  return {
    get toasts() {
      return toasts
    },
    push,
    dismiss,
  }
}

/**
 * Tailwind classes for a toast pill, by tone.
 *
 * `surface` picks which of two treatments to key off: 'solid' is
 * QueueMode/LabelMode's filled pill, 'muted' is TimelineMode's quieter one
 * over the boundary editor. Both now resolve through the design tokens
 * (app.css) rather than raw palette steps, so a toast cannot drift from the
 * rest of the app: 'error' is --color-danger. 'info' carries no hue at all,
 * because the chrome no longer has an accent to spend on "progressing, not
 * broken" -- it is the same fg-on-dark the primary buttons and the progress
 * bars now use, and contrast rather than colour is what separates it from
 * the error tone.
 *
 * Solid pills set an explicit dark label, and the numbers are why. Both
 * fills are light: white-on-fill measures 3.1:1 on danger and 1.2:1 on fg,
 * while bg-on-fill clears 6.3:1 and 16.5:1. The figures master measured for
 * `accent` are gone with the token -- `fg` replaced it as the info fill, and
 * it is the more extreme case of the same argument.
 */
export function toastToneClasses(tone: ToastTone, surface: 'solid' | 'muted' = 'solid'): string {
  if (surface === 'muted') {
    return tone === 'error' ? 'bg-danger/20 text-danger' : 'bg-fg/20 text-fg'
  }
  return tone === 'error' ? 'bg-danger text-bg' : 'bg-fg text-bg'
}
