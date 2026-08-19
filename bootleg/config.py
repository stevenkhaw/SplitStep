import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class LibraryNotMounted(Exception):
    """The library root is absent or not writable."""


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
        return cls(root=root)

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
