from fastapi import FastAPI

from bootleg.config import Library
from bootleg.db.schema import connect, migrate

from .routes import router


def create_app(library: Library) -> FastAPI:
    app = FastAPI(title="BootlegVision", version="0.1.0")
    conn = connect(library.db_path)
    migrate(conn)
    app.state.library = library
    app.state.conn = conn
    app.include_router(router)
    return app
