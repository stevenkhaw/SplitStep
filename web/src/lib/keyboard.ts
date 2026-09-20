/**
 * True when `target` is (or captures typed input like) a form control --
 * an <input>, <textarea>, <select>, or any contenteditable element.
 *
 * QueueMode and TimelineMode both bind their single-letter keybindings on
 * `window` (`<svelte:window onkeydown>`), and Session.svelte deliberately
 * mounts QuadEditor's preset-name input and DetectionPanel's/TimelineMode's
 * threshold sliders alongside whichever mode is active (outside the mode's
 * own `{#key}` block -- see Session.svelte's comment on why). Without this
 * guard, every keystroke typed into one of those fields also matches
 * whichever single-letter shortcut it happens to be and mutates review
 * state the user never meant to touch (e.g. typing "r" in a preset name
 * replays the video; the space bar for "Court 1" gets eaten by play/pause's
 * preventDefault before it ever reaches the field).
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  switch (target.tagName) {
    case 'INPUT':
    case 'TEXTAREA':
    case 'SELECT':
      return true
    default:
      return false
  }
}
