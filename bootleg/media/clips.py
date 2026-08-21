import re


def clip_relpath(source_idx: int, start_ms: int, end_ms: int) -> str:
    """A clip's filename within its session's `clips/` directory.

    Derived from the span, never from `rallies.idx`: `_renumber` reassigns idx
    across a whole session on every `replace_rallies`, so a name built from it
    silently points at a different rally after any threshold sweep.

    Span-derived names buy three things. They survive re-segmentation; they are
    self-identifying on disk, so an orphan can be read rather than guessed at;
    and they make export incremental by construction -- a clip either exists at
    the path its bounds imply, or it does not. There is no separate staleness
    record to keep in sync, which is why this is a pure function and not a
    database column.
    """
    return f"{source_idx:02d}-{start_ms}-{end_ms}.mp4"


# Exactly what clip_relpath writes and nothing else: two digits of source
# index, two non-negative millisecond bounds, ".mp4". Anchored at both ends
# so a name with an extra segment cannot match a prefix of it.
_CLIP_NAME = re.compile(r"^(\d{2,})-(\d+)-(\d+)\.mp4$")


def parse_clip_name(name: str) -> tuple[int, int, int] | None:
    """`(source_idx, start_ms, end_ms)` for a clip filename, else None.

    The inverse of `clip_relpath`, and what makes a stray clip on disk
    self-identifying rather than guessed at. Deliberately strict, because
    the orphan sweep deletes what this claims: anything that is not exactly
    a name this library wrote reads as None rather than as a best guess, so
    someone else's file sitting in `clips/` is never swept up with ours.

    That strictness is also what excludes an encode in flight. `make_clip`
    writes to a dot-prefixed `.part` sibling and only `os.replace()`s it
    onto the real name on success, and such a name carries extra segments
    the pattern does not admit -- so a live encode's output can be neither
    mistaken for a finished clip nor deleted as an orphan.
    """
    match = _CLIP_NAME.match(name)
    if match is None:
        return None
    idx, start_ms, end_ms = match.groups()
    return int(idx), int(start_ms), int(end_ms)
