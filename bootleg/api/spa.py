import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built Svelte bundle at the root.

    Mounted last so /api and /media keep priority. A missing dist is a
    warning, not an error -- `bootleg serve` must still run before the UI
    has ever been built.
    """
    if not (dist / "index.html").is_file():
        log.warning("no built UI at %s; API only. Run `npm run build` in web/.", dist)
        return
    app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
