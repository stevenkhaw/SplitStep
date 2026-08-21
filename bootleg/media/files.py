from pathlib import Path


def find_original(src_dir: Path) -> Path | None:
    """Locate the original file ingest preserved for this source, if any.

    handle_ingest moves the dropped file to `original{suffix}` keeping
    whatever extension the phone gave it (`.mov`, `.mp4`, `.hevc`, ...) --
    the ingest pipeline never re-encodes it -- so the extension can't be
    known ahead of time and has to be discovered by globbing. Returns None
    when the original is not on disk (has_original=0) or before ingest has
    moved it into place. Nothing in the app deletes an original -- Reclaim
    Space was considered and rejected -- so has_original=0 means a file lost
    outside the app, not a state the pipeline produces.
    """
    matches = sorted(src_dir.glob("original.*"))
    return matches[0] if matches else None
