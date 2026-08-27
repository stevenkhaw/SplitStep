/**
 * Failed-job dismissals, shared by every JobsBadge instance.
 *
 * Module-level on purpose: the badge mounts independently in each route's
 * header, and per-instance state made every navigation resurrect every
 * dismissed failure. In-memory only stays deliberate -- a restart
 * re-listing old failures is honest, not a bug.
 *
 * Retrying a job clears its dismissal: jobq.retry reuses the row id, so a
 * retried job that fails AGAIN would otherwise inherit the old dismissal
 * and vanish -- hiding fresh information behind a stale "stop showing me
 * this corpse".
 */
let ids = $state<ReadonlySet<string>>(new Set())

export const dismissals = {
  get ids() {
    return ids
  },
  add(id: string): void {
    ids = new Set([...ids, id])
  },
  clear(id: string): void {
    if (!ids.has(id)) return
    const next = new Set(ids)
    next.delete(id)
    ids = next
  },
}
