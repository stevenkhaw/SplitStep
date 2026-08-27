import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

log = logging.getLogger(__name__)


class _SpaStaticFiles(StaticFiles):
    """StaticFiles that forbids caching index.html without revalidation.

    StaticFiles sends ETag/Last-Modified but no Cache-Control, so browsers
    fall back to heuristic freshness -- and an index.html whose mtime is a
    day old gets served from cache for hours, still naming the PREVIOUS
    build's hashed bundle. Measured live (2026-08-26): a rebuilt UI was on
    disk and served to new visitors while an open browser kept running the
    old app through restarts and plain refreshes, with only a hard refresh
    recovering. `no-cache` means "revalidate every time", not "don't cache":
    the 304 path stays, so the cost is one conditional request per load.
    The hashed assets need no such header -- a new build names new files.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if "text/html" in (response.headers.get("content-type") or ""):
            response.headers["Cache-Control"] = "no-cache"
        return response

_NO_UI_PAGE = """<!doctype html><meta charset="utf-8"><title>SplitStep</title>
<body style="font-family: system-ui; margin: 4rem auto; max-width: 32rem">
<h1>SplitStep is running, but the UI is not built</h1>
<p>The API is up. To get the interface, run <code>npm run build</code> in
<code>web/</code> and restart <code>splitstep serve</code>.</p></body>"""


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built Svelte bundle at the root.

    Mounted last so /api and /media keep priority. A missing dist used to be
    a log-line warning and a blank browser page -- under a non-editable
    install that combination read as "the app is broken" with no clue where.
    Serving an explanation page keeps `splitstep serve` usable before the
    first `npm run build` while making the failure impossible to miss.
    """
    if not (dist / "index.html").is_file():
        log.error("no built UI at %s; serving instructions page. "
                  "Run `npm run build` in web/.", dist)

        @app.get("/", include_in_schema=False)
        def no_ui() -> HTMLResponse:
            return HTMLResponse(_NO_UI_PAGE, status_code=503)

        return
    app.mount("/", _SpaStaticFiles(directory=dist, html=True), name="spa")
