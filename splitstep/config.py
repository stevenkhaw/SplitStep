import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from splitstep.db.schema import connect, migrate


class LibraryNotMounted(Exception):
    """The library root is absent, not writable, or not yet initialized."""


class LibraryAlreadyInitialized(Exception):
    """A library already exists at this path."""


class NotEnoughSpace(Exception):
    """The library volume cannot hold the pending write."""


@dataclass(frozen=True)
class Library:
    root: Path

    @classmethod
    def open(cls, root: Path) -> "Library":
        root = Path(root)
        if not root.is_dir():
            raise LibraryNotMounted(
                f"Library root not found: {root}. Is the drive plugged in?"
            )
        if not os.access(root, os.W_OK):
            raise LibraryNotMounted(f"Library root is not writable: {root}")
        db_path = root / "library.db"
        if not db_path.exists():
            # A leftover, empty mountpoint after an unclean eject looks
            # exactly like a valid root by the checks above. Requiring
            # library.db too is what turns that into an error instead of a
            # silent second library on internal storage -- sqlite3.connect()
            # would otherwise create it right here.
            raise LibraryNotMounted(
                f"No library.db at {root} -- run `splitstep --library {root} init` "
                f"first, or check that the right drive is mounted."
            )
        return cls(root=root)

    @classmethod
    def create(cls, root: Path) -> "Library":
        """Initialize a new library tree and database at an already-mounted
        root. This is `splitstep init`; nothing else is allowed to do this --
        refuses if a library already exists here.
        """
        root = Path(root)
        if not root.is_dir():
            raise LibraryNotMounted(
                f"Library root not found: {root}. Is the drive plugged in?"
            )
        db_path = root / "library.db"
        if db_path.exists():
            raise LibraryAlreadyInitialized(f"Library already initialized at {root}")
        for sub in ("_inbox", "sessions", "reels"):
            (root / sub).mkdir(exist_ok=True)
        conn = connect(db_path)
        try:
            migrate(conn)
        finally:
            conn.close()
        return cls(root=root)

    @classmethod
    def open_or_create(cls, root: Path) -> "Library":
        """Open the library at `root`, initializing it first if the directory
        exists but holds no library.db.

        This is the first-run path for the app's folder picker: the user just
        chose the directory, so "no library.db here" means "make me one", not
        "the drive fell off". `open()`'s refusal stays untouched for every
        other caller -- an unattended `serve` after an unclean eject must
        still fail loudly rather than build a second library on internal
        storage. The directory itself must already exist; nothing here ever
        mkdirs a root (see InboxWatcher.start for the hazard).
        """
        root = Path(root)
        if (root / "library.db").exists():
            return cls.open(root)
        return cls.create(root)

    @property
    def db_path(self) -> Path:
        return self.root / "library.db"

    @property
    def inbox(self) -> Path:
        return self.root / "_inbox"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def reels_dir(self) -> Path:
        return self.root / "reels"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def source_dir(self, session_id: str, idx: int) -> Path:
        return self.session_dir(session_id) / "sources" / f"{idx:02d}"

    def clips_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "clips"

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def require_free(self, need_bytes: int) -> None:
        """Refuse to start a write that cannot finish.

        A 4K clip that dies at 90% is worse than a job that never starts.
        """
        free = self.free_bytes()
        if free < need_bytes:
            raise NotEnoughSpace(
                f"Need {need_bytes / 1e9:.1f} GB of free space on {self.root}, "
                f"only {free / 1e9:.1f} GB available."
            )
