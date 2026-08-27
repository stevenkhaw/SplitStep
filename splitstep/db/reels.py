import re
import sqlite3
import uuid
from datetime import UTC, datetime

Span = tuple[str, int, int]

# Short enough to burn legibly beneath the counter at 4K and to fit the
# builder's inline field -- the same trim-then-measure rule rally notes use.
ITEM_NOTE_MAX_CHARS = 40


def _now() -> str:
    return datetime.now(UTC).isoformat()


def slugify(name: str) -> str:
    """A URL-safe slug for `name`, as the /reels/:slug route spells it.

    Falls back to "reel" for a name with nothing sluggable in it (an emoji,
    punctuation alone): reels.slug is NOT NULL UNIQUE and the name is the
    user's to choose, so an empty slug would turn a legal name into a 500.
    Uniqueness is unique_slug's job, not this function's -- keeping this one
    pure means the route can show a preview of the slug without a database.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "reel"


def unique_slug(conn: sqlite3.Connection, base: str) -> str:
    """`base`, or `base-2`, `base-3` ... if it is already taken.

    reels.slug is UNIQUE but a NAME is free to repeat -- two sessions'
    "points" reels, or a hand-made reel that happens to slug the same as a
    generated one. The suffix is what keeps a legal name from being refused
    over a URL detail the user never chose.
    """
    slug = base
    n = 1
    while conn.execute("SELECT 1 FROM reels WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_reel(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    reel_id = uuid.uuid4().hex
    slug = unique_slug(conn, slugify(name))
    conn.execute(
        "INSERT INTO reels (id,name,slug,dirty,created_at) VALUES (?,?,?,1,?)",
        (reel_id, name, slug, _now()),
    )
    conn.commit()
    return get_reel(conn, reel_id)


def get_reel(conn: sqlite3.Connection, reel_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reels WHERE id = ?", (reel_id,)).fetchone()


def get_reel_by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reels WHERE slug = ?", (slug,)).fetchone()


def find_reel_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """The oldest reel with exactly this name, or None.

    How the session-set buttons find the reel to merge into on a second
    click (see §6.2). Oldest rather than newest so repeated clicking keeps
    converging on one reel instead of walking down a chain of near-duplicates
    a slug collision created.
    """
    return conn.execute(
        "SELECT * FROM reels WHERE name = ? ORDER BY created_at, id LIMIT 1", (name,)
    ).fetchone()


def list_reels(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every reel, newest first, each carrying its item_count.

    One LEFT JOIN rather than a count query per reel: the list page renders
    the count on every row, and N+1 queries behind a route that already runs
    on a shared worker thread is the shape to avoid.
    """
    return conn.execute(
        "SELECT r.*, COUNT(i.reel_id) AS item_count FROM reels r"
        " LEFT JOIN reel_items i ON i.reel_id = r.id"
        " GROUP BY r.id ORDER BY r.created_at DESC, r.id"
    ).fetchall()


def list_items(conn: sqlite3.Connection, reel_id: str) -> list[sqlite3.Row]:
    # position, then the span, so a reel whose positions gapped or tied
    # (removal leaves gaps by design) still renders in a stable order rather
    # than in whatever sqlite happens to return.
    return conn.execute(
        "SELECT * FROM reel_items WHERE reel_id = ?"
        " ORDER BY position, start_ms, end_ms, source_id",
        (reel_id,),
    ).fetchall()


def _keys(conn: sqlite3.Connection, reel_id: str) -> set[Span]:
    return {
        (r["source_id"], r["start_ms"], r["end_ms"]) for r in list_items(conn, reel_id)
    }


def add_items(conn: sqlite3.Connection, reel_id: str, spans: list[Span]) -> int:
    """Append `spans` that are not already in the reel. Returns how many.

    Additive by contract, never a replacement: existing entries and the order
    a human dragged them into are untouched, and nothing is removed. This is
    §6.2's second-click behaviour, and the reason it is not "set membership"
    is that overwriting would silently discard a manual reorder.

    Deliberately NOT `INSERT OR IGNORE`: an ignored row would still have
    consumed the position counter, leaving gaps that read as an ordering the
    user never made. Filtering first means every position handed out lands.
    """
    existing = _keys(conn, reel_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM reel_items WHERE reel_id = ?",
        (reel_id,),
    ).fetchone()
    position = row["m"] + 1

    added = 0
    try:
        for source_id, start_ms, end_ms in spans:
            key = (source_id, start_ms, end_ms)
            # `existing` is updated as we go so a span repeated WITHIN one
            # call is skipped too -- the picker can hand us the same rally
            # twice, and a PRIMARY KEY violation mid-loop would abort an
            # otherwise good batch.
            if key in existing:
                continue
            existing.add(key)
            conn.execute(
                "INSERT INTO reel_items (reel_id,source_id,start_ms,end_ms,position)"
                " VALUES (?,?,?,?,?)",
                (reel_id, source_id, start_ms, end_ms, position),
            )
            position += 1
            added += 1
        if added:
            _mark_dirty(conn, reel_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return added


def remove_item(
    conn: sqlite3.Connection, reel_id: str, source_id: str, start_ms: int, end_ms: int
) -> bool:
    """Drop one span. Returns whether it was there.

    Positions are left gapped rather than compacted: nothing reads position
    as a rank, only as a sort key, and a compaction pass would be a second
    writer of the column for no gain. add_items appends past MAX(position),
    so a gap can never collide with a later insert.
    """
    cur = conn.execute(
        "DELETE FROM reel_items WHERE reel_id = ? AND source_id = ?"
        " AND start_ms = ? AND end_ms = ?",
        (reel_id, source_id, start_ms, end_ms),
    )
    if cur.rowcount:
        _mark_dirty(conn, reel_id)
    conn.commit()
    return cur.rowcount > 0


def set_item_note(
    conn: sqlite3.Connection, reel_id: str, source_id: str,
    start_ms: int, end_ms: int, note: str,
) -> bool:
    """Set one item's note, keyed by the span like every reel-item write.
    False when the reel has no such item -- the caller turns that into a 404
    rather than this function guessing.

    Marks the reel dirty like the membership writes do: a numbered render
    burns the note into the file, so a changed note means the last render no
    longer shows what the reel now says. For a plain render the flag is
    slightly over-eager -- nothing burned changes -- but dirty means "a
    re-render would differ from the file on disk", and whether it would
    differ depends on the render kind nothing here records. Over-warning
    costs one re-render; under-warning ships a reel whose caption lies.
    """
    cur = conn.execute(
        "UPDATE reel_items SET note = ? WHERE reel_id = ? AND source_id = ?"
        " AND start_ms = ? AND end_ms = ?",
        (note, reel_id, source_id, start_ms, end_ms),
    )
    if cur.rowcount == 1:
        _mark_dirty(conn, reel_id)
    conn.commit()
    return cur.rowcount == 1


def set_order(conn: sqlite3.Connection, reel_id: str, spans: list[Span]) -> None:
    """Rewrite every position from the given order.

    Refuses a list that is not exactly the reel's current membership. A
    reorder that adds or drops a span is a bug in the caller (a stale client
    list racing a removal, most likely), and applying it partially would
    leave the reel holding an order describing something other than what it
    contains -- silently, since the UI renders whatever comes back.

    A single pass suffices, unlike rallies._renumber's two-phase dance:
    position carries no UNIQUE constraint here (see the migration), so an
    intermediate state where two rows briefly share a value is legal.
    """
    current = _keys(conn, reel_id)
    given = list(spans)
    if len(given) != len(current) or set(given) != current:
        raise ValueError(
            f"reorder must list exactly the reel's {len(current)} item(s), got {len(given)}"
        )
    try:
        for position, (source_id, start_ms, end_ms) in enumerate(given):
            conn.execute(
                "UPDATE reel_items SET position = ? WHERE reel_id = ? AND source_id = ?"
                " AND start_ms = ? AND end_ms = ?",
                (position, reel_id, source_id, start_ms, end_ms),
            )
        _mark_dirty(conn, reel_id)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def rename_reel(conn: sqlite3.Connection, reel_id: str, name: str) -> None:
    """Change the display name only -- never the slug.

    The slug is baked into the reel's URL and names the rendered file on
    disk (`reels/<slug>.mp4`); keeping it stable across a rename means an
    already-open URL stays valid and the file on disk keeps matching this
    row, with nothing to re-link or orphan. Renaming to "Best of August" and
    having the URL still read `2026-08-18-points` is the deliberate result,
    not a bug -- slugify()/unique_slug are create_reel's job, not this one's.

    Also does not touch dirty, rendered_path or rendered_at: a rename
    changes nothing about what was rendered, so marking the reel dirty here
    would falsely demand a re-encode nobody asked for.
    """
    conn.execute("UPDATE reels SET name = ? WHERE id = ?", (name, reel_id))
    conn.commit()


def delete_reel(conn: sqlite3.Connection, reel_id: str) -> None:
    """Delete the row. reel_items cascades on reel_id (ON DELETE CASCADE,
    see 007_reel_items_by_span.sql) -- covered by test rather than assumed,
    the same caution that put rally_labels' *missing* foreign key comment in
    003: a cascade in the wrong place silently empties a table, and getting
    that backwards is exactly the class of bug this project has hit before.

    Never touches the rendered file on disk -- that needs a Library to
    resolve a path, not just a connection, so it lives as
    `splitstep.reels.delete_rendered_file` instead. The route calls both.
    """
    conn.execute("DELETE FROM reels WHERE id = ?", (reel_id,))
    conn.commit()


def _mark_dirty(conn: sqlite3.Connection, reel_id: str) -> None:
    """Set dirty WITHOUT committing -- for callers already inside a transaction."""
    conn.execute("UPDATE reels SET dirty = 1 WHERE id = ?", (reel_id,))


def mark_dirty(conn: sqlite3.Connection, reel_id: str) -> None:
    _mark_dirty(conn, reel_id)
    conn.commit()


def mark_rendered(
    conn: sqlite3.Connection, reel_id: str, rendered_path: str, membership: set[Span]
) -> None:
    """Record a successful render, and clear dirty only if `membership` --
    what was ACTUALLY rendered -- still matches the reel's current items.

    rendered_path is library-relative, like rallies.clip_path: the drive
    mounts at a different point on each machine, so an absolute path stored
    here would be wrong the first time the library moves.

    handle_reel resolves items once, at the top of a run that can take
    minutes on a re-encode fallback. An add_items / remove_item / set_order
    landing in that window sets dirty = 1 for a reason that is still true
    when this runs: the file just written does not contain what the reel
    now holds. Clearing dirty unconditionally -- what this did before --
    would silently discard that signal, and the UI would read "rendered"
    for a file already stale by the time the render finished. Recovery from
    a wrongly-set dirty flag is one re-render; recovery from a wrongly-CLEARED
    one is a user trusting a file that lied, so leaving dirty SET on any
    mismatch is the honest failure mode, not a cleverer reconciliation.
    """
    conn.execute(
        "UPDATE reels SET rendered_path = ?, rendered_at = ? WHERE id = ?",
        (rendered_path, _now(), reel_id),
    )
    if _keys(conn, reel_id) == membership:
        conn.execute("UPDATE reels SET dirty = 0 WHERE id = ?", (reel_id,))
    conn.commit()
