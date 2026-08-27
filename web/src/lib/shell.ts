/** The few things only the desktop shell can do.
 *
 * The bridge object is injected on every navigation, including the one to the
 * Python server, so the app tier can ask the shell for what a page cannot do
 * itself. In the browser tier it is simply absent, and every shell-only
 * affordance hides rather than failing on click.
 */
type ShellBridge = { backToChooser(): Promise<void> }

function bridge(): ShellBridge | null {
  const found = (globalThis as Record<string, unknown>).__SPLITSTEP_BRIDGE__
  return (found as ShellBridge) ?? null
}

export function inShell(): boolean {
  return bridge() !== null
}

/** Tear down the sidecar and return to the library chooser.
 *
 * Re-point, never move: the library being left is untouched and already in
 * the chooser's list, so coming back to it is one click.
 */
export async function changeLibrary(): Promise<void> {
  const found = bridge()
  if (!found) throw new Error('Changing library needs the desktop app.')
  await found.backToChooser()
}
