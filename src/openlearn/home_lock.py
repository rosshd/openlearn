"""Cross-process coordination for whole-home reads and persistent writes."""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path


def _select_lock_primitives(platform: str = sys.platform):
    if platform == "win32":
        import msvcrt

        locking = getattr(msvcrt, "locking")
        lock_nonblocking = getattr(msvcrt, "LK_NBLCK")
        unlock_mode = getattr(msvcrt, "LK_UNLCK")

        def lock(handle) -> None:
            while True:
                handle.seek(0)
                try:
                    locking(handle.fileno(), lock_nonblocking, 1)
                    return
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EDEADLK) and getattr(
                        exc, "winerror", None
                    ) not in (33, 36):
                        raise
                    time.sleep(0.05)

        def unlock(handle) -> None:
            handle.seek(0)
            locking(handle.fileno(), unlock_mode, 1)

    else:
        import fcntl

        def lock(handle) -> None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

        def unlock(handle) -> None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    return lock, unlock


_LOCK, _UNLOCK = _select_lock_primitives()
_DEPTHS = threading.local()


def lifecycle_lock_path(home: Path) -> Path:
    """Keep the lock outside the home so deleting or moving data cannot remove it."""
    resolved = home.expanduser().resolve(strict=False)
    identity = hashlib.sha256(os.fsencode(os.path.normcase(str(resolved)))).hexdigest()[:16]
    return resolved.parent / f".openlearn-home-{identity}.lifecycle.lock"


@contextlib.contextmanager
def home_lifecycle_lock(home: Path) -> Iterator[None]:
    """Serialize snapshots, destructive lifecycle work, and persistent writers."""
    lock_path = lifecycle_lock_path(home)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path)
    depths = getattr(_DEPTHS, "values", None)
    if depths is None:
        depths = {}
        _DEPTHS.values = depths
    depth = depths.get(key, 0)
    if depth:
        depths[key] = depth + 1
        try:
            yield
        finally:
            depths[key] -= 1
        return
    with lock_path.open("a+b") as handle:
        _LOCK(handle)
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
            _UNLOCK(handle)
