import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

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
    app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
