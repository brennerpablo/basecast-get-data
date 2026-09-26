"""In-memory index of the raw layer, built from the `_manifest.json` in every `dt=` folder.

basecast-airflow writes one manifest per `raw/source=<id>/dt=<date>/` folder, with one entry per file
(URL, fetch time, sha256, bytes, HTTP status, source-specific `meta`). The manifest is the record of
the raw layer, so the index needs no bucket listing beyond finding the manifests (293 today), and it
carries provenance that a listing would not.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import orjson

from basecast_get_data.lake.kinds import kind_of
from basecast_get_data.lake.storage import Storage

log = logging.getLogger(__name__)

RAW = "raw/"


@dataclass(frozen=True)
class RawObject:
    key: str
    source_id: str
    dt: str
    file: str
    bytes: int
    sha256: str | None
    fetched_at: datetime | None
    url: str | None = None
    source_page: str | None = None
    content_type: str | None = None
    http_status: int | None = None
    doc_id: str | None = None
    report_type_id: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return kind_of(self.file)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_manifest(manifest_key: str, raw: bytes) -> list[RawObject]:
    """Entries of one manifest. The folder comes from the key, not the payload, so a manifest copied
    between folders cannot point outside its own."""
    folder = manifest_key.rsplit("/", 1)[0]
    parts = dict(p.split("=", 1) for p in folder.split("/")[1:] if "=" in p)
    source_id, dt = parts.get("source"), parts.get("dt")
    if not source_id or not dt:
        return []
    doc = orjson.loads(raw)
    out = []
    for e in doc.get("entries", []):
        name = e.get("file")
        if not name or "/" in name:
            continue
        status = e.get("http_status")
        out.append(
            RawObject(
                key=f"{folder}/{name}",
                source_id=source_id,
                dt=dt,
                file=name,
                bytes=int(e.get("bytes") or 0),
                sha256=e.get("sha256"),
                fetched_at=_parse_ts(e.get("fetched_at")),
                url=e.get("url"),
                source_page=e.get("source_page"),
                content_type=e.get("content_type"),
                http_status=int(status) if isinstance(status, int | float) else None,
                doc_id=_str(e.get("doc_id")),
                report_type_id=_str(e.get("report_type_id")),
                etag=e.get("etag"),
                last_modified=e.get("last_modified"),
                meta=e.get("meta") or {},
            )
        )
    return out


@dataclass
class Snapshot:
    built_at: datetime
    objects: list[RawObject]
    by_key: dict[str, RawObject]
    by_source: dict[str, list[RawObject]]


def _snapshot(objects: list[RawObject]) -> Snapshot:
    objects.sort(key=lambda o: (o.source_id, o.dt, o.file))
    by_source: dict[str, list[RawObject]] = defaultdict(list)
    for o in objects:
        by_source[o.source_id].append(o)
    return Snapshot(datetime.now(UTC), objects, {o.key: o for o in objects}, dict(by_source))


class LakeIndex:
    def __init__(self, storage: Storage, ttl_s: int):
        self.storage = storage
        self.ttl_s = ttl_s
        self._snap: Snapshot | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()
        self._refreshing = False

    def build(self) -> Snapshot:
        keys = self.storage.glob(RAW, "_manifest.json")

        def load(key: str) -> list[RawObject]:
            try:
                return parse_manifest(key, self.storage.read_bytes(key))
            except Exception:
                log.exception("unreadable manifest %s", key)
                return []

        with ThreadPoolExecutor(max_workers=16) as pool:
            objects = [o for chunk in pool.map(load, keys) for o in chunk]
        return _snapshot(objects)

    def snapshot(self) -> Snapshot:
        """The current index. The first call builds it; later calls past the TTL get the old one while
        a background thread rebuilds it."""
        if self._snap is None:
            with self._lock:
                if self._snap is None:
                    self._snap = self.build()
                    self._loaded_at = time.monotonic()
        elif time.monotonic() - self._loaded_at > self.ttl_s and not self._refreshing:
            self._refreshing = True
            threading.Thread(target=self._refresh, daemon=True).start()
        return self._snap

    def _refresh(self) -> None:
        try:
            snap = self.build()
            self._snap, self._loaded_at = snap, time.monotonic()
        except Exception:
            log.exception("lake index refresh failed; keeping the previous one")
            self._loaded_at = time.monotonic()
        finally:
            self._refreshing = False
