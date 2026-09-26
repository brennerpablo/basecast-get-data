"""The lake behind the routers: the manifest index, the storage and the parsed-file cache.

Raw files never change under a key (a new version gets a new name), so a parse is done once per file,
sheet or zip member and kept in memory until the byte budget pushes it out.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from basecast_get_data.config import get_settings
from basecast_get_data.lake import readers
from basecast_get_data.lake.index import LakeIndex, RawObject
from basecast_get_data.lake.kinds import TABULAR, TEXTUAL, kind_of
from basecast_get_data.lake.storage import Storage, check_key, make_storage


class TooLarge(Exception):
    pass


class ParsedCache:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._items: OrderedDict[tuple, readers.Parsed] = OrderedDict()
        self._sizes: dict[tuple, int] = {}
        self._total = 0
        self._lock = threading.Lock()

    def get(self, key: tuple) -> readers.Parsed | None:
        with self._lock:
            item = self._items.get(key)
            if item is not None:
                self._items.move_to_end(key)
            return item

    def put(self, key: tuple, value: readers.Parsed) -> None:
        size = int(value.frame.estimated_size())
        with self._lock:
            if key in self._items:
                return
            self._items[key] = value
            self._sizes[key] = size
            self._total += size
            while self._total > self.max_bytes and len(self._items) > 1:
                old, _ = self._items.popitem(last=False)
                self._total -= self._sizes.pop(old)


@dataclass
class Target:
    """A file to read: a raw object, or one member of a zip."""

    obj: RawObject
    member: str | None

    @property
    def name(self) -> str:
        return self.member.rsplit("/", 1)[-1] if self.member else self.obj.file

    @property
    def kind(self) -> str:
        return kind_of(self.name)

    @property
    def cache_key(self) -> tuple:
        return (self.obj.key, self.obj.sha256 or "", self.member or "")


class Lake:
    def __init__(self, storage: Storage):
        settings = get_settings()
        self.storage = storage
        self.index = LakeIndex(storage, settings.index_ttl_s)
        self.parsed = ParsedCache(max_bytes=settings.cache_max_bytes // 2)
        self.preview_max_bytes = settings.preview_max_bytes
        self.members_dir = settings.cache_dir / "members"

    def object(self, key: str) -> RawObject:
        """The raw object under a key. Keys outside `raw/` (parquet/, derived/) get a minimal record."""
        check_key(key)
        obj = self.index.snapshot().by_key.get(key)
        if obj is not None:
            return obj
        if key.startswith("raw/"):
            raise FileNotFoundError(key)
        size = self.storage.size(key)
        parts = dict(p.split("=", 1) for p in key.split("/")[:-1] if "=" in p)
        return RawObject(
            key=key,
            source_id=parts.get("source", key.split("/")[0]),
            dt=parts.get("dt", ""),
            file=key.rsplit("/", 1)[-1],
            bytes=size,
            sha256=None,
            fetched_at=None,
        )

    def local_file(self, target: Target) -> Path:
        if target.obj.bytes > self.preview_max_bytes:
            raise TooLarge(target.obj.key)
        path = self.storage.local_path(target.obj.key)
        if not target.member:
            return path
        digest = hashlib.sha256("|".join(target.cache_key).encode()).hexdigest()[:24]
        dest = self.members_dir / digest / target.name
        if dest.is_file():
            return dest
        return readers.extract_member(path, target.member, dest)

    def tabular(self, target: Target, sheet: str | None) -> readers.Parsed:
        if target.kind not in TABULAR:
            raise readers.NotTabular(f"no grid view for {target.kind}")
        path = self.local_file(target)
        if target.kind == "sheet":
            sheet = sheet or readers.sheet_names(path)[0]
        key = (*target.cache_key, sheet or "")
        parsed = self.parsed.get(key)
        if parsed is None:
            parsed = readers.read_tabular(path, target.kind, sheet)
            self.parsed.put(key, parsed)
        return parsed

    def sheets(self, target: Target) -> list[readers.SheetInfo]:
        """Sheet names with their sizes. Sizes need a full parse, so big workbooks get them only for the
        sheets already loaded."""
        path = self.local_file(target)
        names = readers.sheet_names(path)
        full = target.obj.bytes <= 8_000_000 or target.member is not None
        out = []
        for name in names:
            key = (*target.cache_key, name)
            parsed = self.parsed.get(key)
            if parsed is None and full:
                parsed = readers.read_sheet(path, name)
                self.parsed.put(key, parsed)
            out.append(
                readers.SheetInfo(
                    name=name,
                    rows=parsed.frame.height if parsed else None,
                    columns=parsed.frame.width if parsed else None,
                )
            )
        return out

    def text(self, target: Target) -> list[readers.TextBlock]:
        if target.kind not in TEXTUAL:
            raise readers.NotTabular(f"no text view for {target.kind}")
        return readers.text_blocks(self.local_file(target), target.kind)


@lru_cache(maxsize=1)
def get_lake() -> Lake:
    return Lake(make_storage(get_settings()))
