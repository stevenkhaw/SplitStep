# Clips grouped by source — design

Date: 2026-08-26
Status: Approved (Steven, 2026-08-26). Lands on `phase1-server-friend-readiness`,
not master — master stays frozen-stable while the phase is tested.

## What changes

A session's clips move from one flat folder to a folder per source file:

```
before: sessions/2026-08-18/clips/01-9000-14000.mp4, 02-3000-8000.mp4, …
after:  sessions/2026-08-18/clips/01/9000-14000.mp4
        sessions/2026-08-18/clips/02/3000-8000.mp4
```

Why: a session (a date) often holds several phone videos, and browsing or
drag-sharing "everything from video 2" in Finder currently means picking
`02-*` names out of a mixed pile. The source index was already in the name;
it becomes the directory.

## What deliberately does not change

- **Span-derived naming and the invariant it buys** — "a clip either exists
  at the path its bounds imply, or it does not." `clip_relpath()` stays the
  single pure function every consumer derives paths from; the path just
  gained a directory. Export incrementality, orphan detection, and reel
  rendering keep resolving through it with no second bookkeeping.
- **Reels** — `reel_items` stores spans, never paths; `resolve_items`
  derives paths at read time, so rendered reels and reel membership are
  untouched.
- **Temp-file discipline** — `make_clip` still writes a dot-prefixed
  `.part` sibling (now inside `NN/`) and `os.replace()`s onto the final
  name; globs and the orphan gate still cannot see an encode in flight.

## Mechanics

1. **`clip_relpath(idx, start, end)`** returns `"{idx:02d}/{start}-{end}.mp4"`.
   Every `clips_dir / clip_relpath(...)` join composes into the subdirectory
   automatically, and `make_clip` already `mkdir -p`s the destination's
   parent, so the encode path needs no change.
2. **`parse_clip_name`** becomes the inverse of the *relpath*, accepting two
   anchored shapes: nested `NN/START-END.mp4` (current) and legacy flat
   `NN-START-END.mp4`. Legacy stays parseable on purpose: a flat file left
   behind by the one-time move's collision case must remain
   self-identifying so `clips orphans`/`prune` can report and sweep it
   instead of it becoming invisible dead weight.
3. **Orphan scan** walks one level of source subdirectories plus the flat
   top level, matching against a `claimed` set of relpaths. The
   strictness rules are unchanged: only names this library writes are ever
   candidates.
4. **One-time layout reconciliation** — `reconcile_clip_layout(library,
   conn)`, run at `serve` startup (before the worker starts) and at the top
   of the `clips` CLI commands. For each flat legacy clip: rename into
   `NN/` (same directory tree, atomic on one filesystem) and rewrite the
   matching `rallies.clip_path` row to the new relative path. If the nested
   target already exists, the flat file is left in place and logged — never
   silently deleted; the orphan tooling now sees it. Idempotent: a swept
   library yields zero moves. Not a sqlite migration — it is file layout,
   and migrations cannot move files.
5. **`_clip_color_profile`'s legacy-clips glob** (`sessions/*/clips/*.mp4`)
   gains the nested pattern, so a pre-migration library still locks to
   legacy HLG whether or not the layout sweep has run yet.
6. **`rallies.clip_path`** rows written from now on carry the nested
   relative path; the sweep rewrites old rows it moves. The
   exact-string operations on the column (orphan delete's
   `WHERE clip_path = ?`, `_carried_clip_path`) are unaffected — they
   compare whatever string was recorded, and the sweep keeps recorded
   strings pointing at real files.

## Testing

Unit: both `clip_relpath` shapes of `parse_clip_name` (nested, legacy flat,
rejects), reconcile (moves + clip_path rewrite, collision leaves the flat
file and logs, idempotent second run), orphan scan across nested + stray
flat. Existing export/clip/reel tests follow `clip_relpath` and update where
they hardcode flat names. Full suite green on the branch.
