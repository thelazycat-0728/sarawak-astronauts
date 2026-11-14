"""A bounded on-disk cache of finished cutout files.

- **Key:** ``<release>_<obs_id>_<band>_<ra>_<dec>_<size>`` with the position rounded
  to 4 decimals of a degree and the size in arcmin to 0.1 (see ``geometry``), for
  example ``dr1_2025W34_2B_0090_4_D2_269.4464_+4.7668_15.2``. Files are
  ``<key>.fits`` in the cache directory.
- **Atomic writes:** a cutout is written to a hidden ``.<key>.<random>.tmp`` file in
  the same directory and renamed into place with ``os.replace``, so readers never
  see a partial file. A failed write leaves no entry and removes its temp files.
- **One download per key:** a per-key lock inside the process makes concurrent
  requests for one key wait for the first, which then finds the file. (Separate
  processes may still download the same key twice; the rename keeps that safe.)
- **Eviction:** after each new file, the oldest files by last use (modification
  time, refreshed on every hit) are deleted until the total size of ``*.fits``
  files is at or under the cap. The file just written is never evicted. Temp files
  older than an hour (left by a crash) are removed at the same time.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from app.models import BAND_IDS

log = logging.getLogger("app.cutouts")

_KEY_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,160}$")
_RELEASE_RE = re.compile(r"^[A-Za-z0-9]{1,16}$")
_OBS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,56}$")  # as app.irsa accepts
STALE_TEMP_S = 3600.0


def cutout_key(
    release: str, obs_id: str, band: str, ra: float, dec: float, size_arcmin: float
) -> str:
    """Cache key from already rounded values; ValueError if unsafe as a file name."""
    if not (
        _RELEASE_RE.fullmatch(release) and _OBS_ID_RE.fullmatch(obs_id) and band in BAND_IDS
    ):
        raise ValueError(f"unusable cache key parts: {release!r}, {obs_id!r}, {band!r}")
    key = f"{release.lower()}_{obs_id}_{band}_{ra:.4f}_{dec:+.4f}_{size_arcmin:.1f}"
    if not _KEY_RE.fullmatch(key) or ".." in key:
        raise ValueError(f"unusable cache key: {key!r}")
    return key


class CutoutCache:
    def __init__(self, directory: str | os.PathLike[str], max_bytes: int):
        self.directory = Path(directory)
        self.max_bytes = int(max_bytes)
        self._guard = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def path_for(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key) or ".." in key:
            raise ValueError(f"unusable cache key: {key!r}")
        return self.directory / f"{key}.fits"

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = self._key_locks[key] = threading.Lock()
            return lock

    @staticmethod
    def _touch(path: Path) -> bool:
        """Mark a hit as recently used; False if the file is gone."""
        try:
            os.utime(path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return path.exists()

    def lookup(self, key: str) -> Path | None:
        path = self.path_for(key)
        return path if path.is_file() and self._touch(path) else None

    def get_or_create(self, key: str, create: Callable[[Path], None]) -> tuple[Path, bool]:
        """Return (path, was_cached). On a miss, ``create(temp_path)`` must write
        the finished file; it is then renamed into place. Exceptions from
        ``create`` propagate and leave no cache entry."""
        hit = self.lookup(key)
        if hit is not None:
            return hit, True
        with self._lock_for(key):
            hit = self.lookup(key)
            if hit is not None:
                return hit, True
            self.directory.mkdir(parents=True, exist_ok=True)
            final = self.path_for(key)
            temp = self.directory / f".{key}.{uuid.uuid4().hex}.tmp"
            try:
                create(temp)
                os.replace(temp, final)
            finally:
                temp.unlink(missing_ok=True)
        self.evict(keep=final)
        return final, False

    def temp_path(self, key: str, label: str) -> Path:
        """Another hidden temp file name in the cache directory (for raw downloads)."""
        self.path_for(key)  # validates the key
        return self.directory / f".{key}.{uuid.uuid4().hex}.{label}.tmp"

    def entries(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return [p for p in self.directory.glob("*.fits") if p.is_file()]

    def total_bytes(self) -> int:
        total = 0
        for path in self.entries():
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def evict(self, keep: Path | None = None) -> int:
        """Delete least recently used files until under the cap; returns files removed."""
        with self._guard:
            stats = []
            for path in self.entries():
                try:
                    st = path.stat()
                except OSError:
                    continue
                stats.append((st.st_mtime, path, st.st_size))
            total = sum(size for _, _, size in stats)
            removed = 0
            for _mtime, path, size in sorted(stats, key=lambda s: (s[0], s[1].name)):
                if total <= self.max_bytes:
                    break
                if keep is not None and path == keep:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:  # for example open elsewhere on Windows
                    log.info("cutout cache: could not evict %s (%s)", path.name, exc)
                    continue
                total -= size
                removed += 1
            self._remove_stale_temps()
            return removed

    def _remove_stale_temps(self) -> None:
        cutoff = time.time() - STALE_TEMP_S
        for path in self.directory.glob(".*.tmp"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
