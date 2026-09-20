import { MIN_RALLY_MS } from './timeline'
import type { Rally } from './types'

/**
 * Splitting one rally into two, and putting the halves back.
 *
 * The detector merges two rallies whenever the break between them scores
 * above threshold. That is the correct trade -- the tuning that separates
 * them shreds real rallies -- but timeline mode could only ever move a
 * merged rally's two boundaries, and there was no third rally to move a
 * boundary to.
 *
 * A hand-made half carries `det_start_ms === null`. That is the whole
 * marker: it keeps the two halves from colliding in the label corpus (which
 * anchors on the detector's span), and it is what `canMerge` tests so an
 * undo can never destroy detector provenance.
 *
 * `applyAdd` lives here rather than in a module of its own for that same
 * marker: a hand-drawn span and a split half are the same kind of row, and
 * both have to renumber by exactly the rule below. Two copies of
 * `_renumber` is already one more than anyone wants; a third would be worse
 * than the coupling.
 *
 * `applySplit`/`applyMerge` exist so TimelineMode can update in place rather
 * than triggering Session's `rallyRevision` remount. That is a UX
 * requirement, not an optimisation: a remount resets the playhead, so a cut
 * at 22.0s would throw the reviewer back to the top of the rally at exactly
 * the moment they want to trim the seam they just made.
 */

/**
 * Whether a cut at `atMs` leaves two rallies worth having.
 *
 * The MIN_RALLY_MS floor lives here and not on the server, which validates
 * only that both halves are non-empty. That is the same division /bounds
 * already draws with clampMinGap, and it is deliberate: media/concat.py
 * spells out that a hand-trimmed clip well under 1.5s is a real input, so
 * the floor is a nudge in the UI rather than a rule about what a library
 * may hold.
 */
export function canSplit(rally: Rally, atMs: number): boolean {
  return atMs - rally.start_ms >= MIN_RALLY_MS && rally.end_ms - atMs >= MIN_RALLY_MS
}

/**
 * Whether `rally` can be absorbed back into `prev`.
 *
 * Mirrors splitstep/db/rallies.py::merge_into_previous exactly, so the key
 * hint can be greyed before the request rather than after a 400. The det
 * test is the load-bearing one: merge may only ever undo something a human
 * made, never delete a row rally_labels is anchored to.
 */
export function canMerge(rally: Rally, prev: Rally | undefined): boolean {
  if (rally.det_start_ms !== null) return false
  if (!prev) return false
  if (prev.source_id !== rally.source_id) return false
  // Exact abutment. Once the seam has been trimmed the two rows no longer
  // describe one contiguous stretch of footage, and rejoining them would
  // invent play across the gap the reviewer just opened.
  return prev.end_ms === rally.start_ms
}

/**
 * Renumber `idx` the way splitstep/db/rallies.py::_renumber does: ordered by
 * the source's own idx, then by start_ms, assigned 1..N across the whole
 * session.
 *
 * Two implementations of one rule is a real cost. The alternative is a
 * remount on every cut (see the module comment), and the rule is frozen by
 * migration 001's UNIQUE(session_id, idx). web/tests/split.test.ts pins the
 * parity.
 *
 * `sourceOrder` -- the session's source ids in `sources.idx` order -- is
 * required, not inferred, because the only inference available (first
 * appearance in `rallies`) is right only sometimes: applySplit removes the
 * split target before calling this, so when the target was its source's
 * sole rally, that source's first appearance moves to wherever the two new
 * halves land. An ordering that is correct on most inputs and wrong on one
 * is worse than no fallback, because it fails exactly where nothing local
 * flags it -- this must agree with splitstep/db/rallies.py::_renumber or
 * the UI prints a rally number the server disagrees with, on a field the
 * reviewer reads aloud.
 */
function renumber(rallies: Rally[], sourceOrder: string[]): Rally[] {
  const rank = new Map(sourceOrder.map((id, i) => [id, i]))
  return [...rallies]
    .sort((a, b) => {
      // Default to 0 (ranks unlisted sources first) only as a fallback for missing sourceOrder entries; this is a caller bug.
      const bySource = (rank.get(a.source_id) ?? 0) - (rank.get(b.source_id) ?? 0)
      return bySource !== 0 ? bySource : a.start_ms - b.start_ms
    })
    .map((r, i) => ({ ...r, idx: i + 1 }))
}

/**
 * The local counterpart of split_rally. `newId` is the id the server
 * returned, not one invented here -- the two lists must agree on it or the
 * next merge would name a rally the server does not have.
 */
export function applySplit(
  rallies: Rally[],
  rallyId: string,
  atMs: number,
  newId: string,
  sourceOrder: string[],
): Rally[] {
  const target = rallies.find((r) => r.id === rallyId)
  if (!target) return rallies
  const first: Rally = { ...target, end_ms: atMs }
  const second: Rally = {
    ...target,
    id: newId,
    start_ms: atMs,
    // No detector ever proposed this rally -- see the module comment.
    det_start_ms: null,
    det_end_ms: null,
  }
  const rest = rallies.filter((r) => r.id !== rallyId)
  return renumber([...rest, first, second], sourceOrder)
}

/** What the server needs no help with, and what it hands back: the span the
 *  reviewer drew, plus the id the row was actually created under. */
export interface AddedRally {
  id: string
  sessionId: string
  sourceId: string
  startMs: number
  endMs: number
}

/**
 * The local counterpart of create_rally -- a span the detector never
 * proposed, inserted into the list in place.
 *
 * Every field but the span is written out here rather than cloned off a
 * neighbouring rally, because a clone is how a new rally would silently
 * arrive starred, rejected, or carrying somebody else's note. The reviewer
 * said only "there is play here"; a default asserting anything more is a
 * judgement nobody made.
 *
 * `det_start_ms`/`det_end_ms` are null for the same reason the second half
 * of a split is (see the module comment): the absence IS the "a human made
 * this" marker, and it is what `canMerge`, `/label`'s refusal and
 * `LabelController`'s filter all read. `confidence` is 0 because a detector
 * score is the detector's claim about its own proposal, and there was no
 * proposal.
 */
export function applyAdd(
  rallies: Rally[],
  added: AddedRally,
  sourceOrder: string[],
): Rally[] {
  const row: Rally = {
    id: added.id,
    session_id: added.sessionId,
    source_id: added.sourceId,
    // Overwritten by renumber below; a placeholder here rather than a
    // guess, so nothing can read a stale idx off this object.
    idx: 0,
    start_ms: added.startMs,
    end_ms: added.endMs,
    det_start_ms: null,
    det_end_ms: null,
    confidence: 0,
    starred: 0,
    rejected: 0,
    point: 0,
    reviewed_at: null,
    seen_at: null,
    note: '',
    winner: '',
  }
  return renumber([...rallies, row], sourceOrder)
}

/**
 * The rally immediately before `rally` in `(source rank, start_ms)` order --
 * the row `U` would merge into. This is the sorted-adjacency answer, not a
 * scan for whatever abuts `rally.start_ms`: the two agree except when two
 * rallies in the same source share an end_ms, which a manual drag can leave
 * behind (`/bounds` only requires `end_ms > start_ms` on the row being
 * moved, not clearance from its neighbours). In that state a raw scan
 * returns whichever tied row happens to come first in `rallies`, an answer
 * that depends on array order and not on the footage.
 *
 * It is exported, not inlined, because three places need the same row:
 * `applyMerge` below rewrites the list around it, the component gates the
 * `U` key with it before the request goes out, and on success the component
 * points `currentId` at it so the reviewer lands on whichever rally actually
 * absorbed the other. The server resolves the same tie with
 * `ORDER BY start_ms DESC LIMIT 1` (merge_into_previous) -- if the client
 * picked differently, the merge would land where the server put it while the
 * screen pointed at the row it never touched, and nothing about that rally
 * would look wrong afterwards.
 */
export function findMergePrev(
  rallies: Rally[],
  rally: Rally,
  sourceOrder: string[],
): Rally | undefined {
  const ordered = renumber(rallies, sourceOrder)
  const i = ordered.findIndex((r) => r.id === rally.id)
  return i > 0 ? ordered[i - 1] : undefined
}

/** The local counterpart of merge_into_previous. */
export function applyMerge(
  rallies: Rally[],
  rallyId: string,
  sourceOrder: string[],
): Rally[] {
  const target = rallies.find((r) => r.id === rallyId)
  if (!target) return rallies
  const prev = findMergePrev(rallies, target, sourceOrder)
  if (!canMerge(target, prev) || !prev) return rallies
  const merged: Rally = { ...prev, end_ms: target.end_ms }
  return renumber(
    rallies.filter((r) => r.id !== rallyId && r.id !== prev.id).concat(merged),
    sourceOrder,
  )
}
