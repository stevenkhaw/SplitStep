/**
 * The first-run flow's step machine: welcome → tips → drop → waiting.
 *
 * Pure data so the walk is testable; FirstRun.svelte is a thin shell over
 * it. 'waiting' is terminal from the machine's point of view -- the card
 * leaves the page not by advancing but by `firstRunVisible` flipping false
 * once the first session appears in the list poll.
 */
export type FirstRunStep = 'welcome' | 'tips' | 'drop' | 'waiting'

const ORDER: FirstRunStep[] = ['welcome', 'tips', 'drop', 'waiting']

export function nextStep(step: FirstRunStep): FirstRunStep {
  return ORDER[Math.min(ORDER.indexOf(step) + 1, ORDER.length - 1)]
}

export function prevStep(step: FirstRunStep): FirstRunStep {
  return ORDER[Math.max(ORDER.indexOf(step) - 1, 0)]
}

/**
 * Loading and genuinely-empty are different states: the welcome card during
 * the fetch would flash at every returning user, which is the same failure
 * the Library page's own `loading` flag exists to prevent.
 */
export function firstRunVisible(sessionCount: number, loading: boolean): boolean {
  return !loading && sessionCount === 0
}
