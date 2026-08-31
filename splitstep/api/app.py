import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from splitstep.config import Library
from splitstep.db.schema import connect, migrate

from .routes import router
from .spa import mount_spa


class ThreadLocalConnections:
    """One sqlite3 connection per calling thread, not one shared across all
    of them.

    Every route is `def`, not `async def`, so Starlette runs each one on an
    anyio worker thread. A single shared connection meant one thread's bare
    `commit()` (e.g. `set_star`) could finalize a transaction a different
    thread had open and had not committed yet -- concretely,
    `replace_rallies`'s explicit transaction getting half-applied by a
    concurrent star/reject during a re-segment. A sqlite3 transaction lives
    on the Connection object that opened it, so giving each thread its own
    connection makes that impossible.
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._all: list[sqlite3.Connection] = []
        # threading.local() can only be reset from the thread that owns it,
        # and close_all() may run on a different thread (the lifespan
        # shutdown task) than the ones that cached connections. A shared
        # generation counter lets every thread's next get() notice its
        # cached connection was invalidated, without needing to touch that
        # thread's local storage from the outside.
        self._generation = 0

    def get(self) -> sqlite3.Connection:
        cached = getattr(self._local, "conn", None)
        cached_generation = getattr(self._local, "generation", None)
        with self._lock:
            current_generation = self._generation
        if cached is not None and cached_generation == current_generation:
            return cached

        conn = connect(self._db_path)
        self._local.conn = conn
        self._local.generation = current_generation
        with self._lock:
            self._all.append(conn)
        return conn

    def close_all(self) -> None:
        with self._lock:
            conns, self._all = self._all, []
            self._generation += 1
        for conn in conns:
            conn.close()


def create_app(library: Library, spa_dist: Path | None = None) -> FastAPI:
    # Migrate once at startup on a throwaway connection, then close it --
    # requests get their own connections from ThreadLocalConnections below.
    conn = connect(library.db_path)
    migrate(conn)
    conn.close()

    conns = ThreadLocalConnections(library.db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        conns.close_all()

    app = FastAPI(title="SplitStep", version="0.2.0", lifespan=lifespan)
    app.state.library = library
    app.state.conns = conns
    app.include_router(router)
    if spa_dist is not None:
        mount_spa(app, spa_dist)
    return app
