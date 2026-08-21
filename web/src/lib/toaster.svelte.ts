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
 * `surface` picks which of two already-shipped colour treatments to key
 * off: 'solid' is QueueMode/LabelMode's mid-opacity pill, 'muted' is
 * TimelineMode's darker one over the boundary editor. 'error' reproduces
 * each surface's existing red exactly, so this refactor changes no pixel on
 * the failure path every consumer already relied on. 'info' extends that
 * same surface's own blue -- the colour this app already uses for
 * "something is progressing, not broken" (JobsBadge's running-job count,
 * the queue/label progress bars, Setup's action button) -- rather than
 * inventing a third hue project-wide.
 */
export function toastToneClasses(tone: ToastTone, surface: 'solid' | 'muted' = 'solid'): string {
  if (surface === 'muted') {
    return tone === 'error' ? 'bg-red-900/90 text-red-100' : 'bg-blue-900/90 text-blue-100'
  }
  return tone === 'error' ? 'bg-red-500/90 text-white' : 'bg-blue-500/90 text-white'
}
