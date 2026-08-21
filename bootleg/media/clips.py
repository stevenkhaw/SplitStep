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
