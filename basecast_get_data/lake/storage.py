"""Read-only access to the lake, on disk or in GCS, under the same keys.

Keys are POSIX paths relative to the lake root (`raw/source=ercot_gis/dt=2026-08-01/<file>`), the same
layout basecast-airflow writes locally and to `gs://basecast-509812-lake`.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from basecast_get_data.config import REPO_ROOT, Settings

CHUNK = 1 << 20


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int


class Storage(Protocol):
    uri: str

    def glob(self, prefix: str, name: str) -> list[str]: ...
    def list_dir(self, prefix: str) -> tuple[list[str], list[ObjectInfo]]: ...
    def read_bytes(self, key: str) -> bytes: ...
    def size(self, key: str) -> int: ...
    def local_path(self, key: str) -> Path: ...
    def iter_range(self, key: str, start: int, end: int) -> Iterator[bytes]: ...
    def signed_url(self, key: str, expires: timedelta, download: bool = False) -> str | None: ...


# The layers the browser may read. Everything else in the bucket stays out of reach, starting with
# backups/ (Postgres dumps) and the pipelines' _logs/, _runs/ and _tmp/.
PUBLIC_LAYERS = ("raw/", "parquet/", "derived/")


def check_key(key: str) -> str:
    """Rejects keys that could leave the lake root or reach a private layer."""
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid key: {key!r}")
    if not key.startswith(PUBLIC_LAYERS):
        raise ValueError(f"not a browsable key: {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.uri = self.root.as_uri()

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def glob(self, prefix: str, name: str) -> list[str]:
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob(name) if p.is_file())

    def list_dir(self, prefix: str) -> tuple[list[str], list[ObjectInfo]]:
        base = self.root / prefix if prefix else self.root
        if not base.is_dir():
            return [], []
        folders, objects = [], []
        for p in sorted(base.iterdir()):
            if p.name.startswith("."):
                continue
            rel = p.relative_to(self.root).as_posix()
            if p.is_dir():
                folders.append(rel + "/")
            else:
                objects.append(ObjectInfo(rel, p.stat().st_size))
        return folders, objects

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def local_path(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path

    def iter_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        with self._path(key).open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    def signed_url(self, key: str, expires: timedelta, download: bool = False) -> str | None:
        return None


class DiskCache:
    """Files copied from GCS for parsing, evicted oldest-first past a byte budget."""

    def __init__(self, root: Path, max_bytes: int):
        self.root = root
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.root / digest / Path(key).name

    def evict(self) -> None:
        with self._lock:
            files = [p for p in self.root.rglob("*") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
            for p in sorted(files, key=lambda p: p.stat().st_atime):
                if total <= self.max_bytes:
                    break
                total -= p.stat().st_size
                p.unlink(missing_ok=True)


class GcsStorage:
    def __init__(self, bucket: str, project: str, cache: DiskCache, signer_email: str = ""):
        from google.cloud import storage

        self.client = storage.Client(project=project)
        self.bucket = self.client.bucket(bucket)
        self.uri = f"gs://{bucket}"
        self.cache = cache
        self.signer_email = signer_email

    def glob(self, prefix: str, name: str) -> list[str]:
        blobs = self.client.list_blobs(self.bucket, prefix=prefix, match_glob=f"**/{name}")
        return sorted(b.name for b in blobs)

    def list_dir(self, prefix: str) -> tuple[list[str], list[ObjectInfo]]:
        it = self.client.list_blobs(self.bucket, prefix=prefix, delimiter="/")
        objects = [ObjectInfo(b.name, b.size or 0) for b in it if b.name != prefix]
        return sorted(it.prefixes), objects

    def read_bytes(self, key: str) -> bytes:
        return self.bucket.blob(check_key(key)).download_as_bytes()

    def size(self, key: str) -> int:
        blob = self.bucket.get_blob(check_key(key))
        if blob is None:
            raise FileNotFoundError(key)
        return blob.size or 0

    def local_path(self, key: str) -> Path:
        path = self.cache.path_for(check_key(key))
        if path.is_file():
            os.utime(path)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        self.bucket.blob(key).download_to_filename(tmp)
        tmp.replace(path)
        self.cache.evict()
        return path

    def iter_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        blob = self.bucket.blob(check_key(key))
        pos = start
        while pos <= end:
            stop = min(end, pos + 8 * CHUNK - 1)
            yield blob.download_as_bytes(start=pos, end=stop)
            pos = stop + 1

    def signed_url(self, key: str, expires: timedelta, download: bool = False) -> str | None:
        """V4 signed URL. On Cloud Run the service account signs through IAM (signBlob on itself); with
        user credentials (local development) signing fails and the caller streams the bytes instead."""
        import google.auth
        from google.auth.transport.requests import Request

        try:
            credentials, _ = google.auth.default()
            credentials.refresh(Request())
            email = self.signer_email or getattr(credentials, "service_account_email", "")
            if not email or email == "default":
                return None
            name = key.rsplit("/", 1)[-1].replace('"', "")
            return self.bucket.blob(check_key(key)).generate_signed_url(
                version="v4",
                expiration=expires,
                method="GET",
                service_account_email=email,
                access_token=credentials.token,
                # A download link saves the file under its own name instead of opening it.
                response_disposition=f'attachment; filename="{name}"' if download else None,
            )
        except Exception:
            return None


def make_storage(settings: Settings) -> Storage:
    root = settings.lake_root
    if root.startswith("gs://"):
        bucket = root.removeprefix("gs://").strip("/")
        return GcsStorage(
            bucket,
            settings.gcp_project,
            DiskCache(settings.cache_dir / "lake", settings.cache_max_bytes),
            settings.signer_email,
        )
    if root.startswith("file://"):
        root = root.removeprefix("file://")
    path = Path(root)
    return LocalStorage(path if path.is_absolute() else REPO_ROOT / path)


def expires_at(expires: timedelta) -> datetime:
    return datetime.now(UTC) + expires
