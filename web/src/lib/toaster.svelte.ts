export interface Toast {
  id: number
  message: string
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

  function push(message: string): number {
    const id = nextId++
    toasts = [...toasts, { id, message }]
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
