import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1024 * 1024


def range_response(path: Path, range_header: str | None) -> Response:
    """Serve a file, honouring a single-range request.

    Without 206 support the <video> element cannot seek at all.
    """
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Not found: {path.name}")

    size = path.stat().st_size
    if not range_header:
        return FileResponse(path, headers={"Accept-Ranges": "bytes"})

    match = RANGE_RE.fullmatch(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Malformed Range header")

    raw_start, raw_end = match.groups()
    if raw_start == "":
        if raw_end == "":
            raise HTTPException(status_code=416, detail="Malformed Range header")
        length = int(raw_end)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1

    if start >= size or end < start:
        raise HTTPException(
            status_code=416, detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
        )
    end = min(end, size - 1)

    def stream():
        remaining = end - start + 1
        with path.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type="video/mp4",
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
        },
    )
