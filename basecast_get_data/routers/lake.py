"""The lake browser: sources, folders, raw objects and their previews."""

from __future__ import annotations

import logging
import mimetypes
import re
import zipfile
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from basecast_get_data.auth import require_token
from basecast_get_data.db import pg
from basecast_get_data.lake import readers
from basecast_get_data.lake.index import RAW, RawObject
from basecast_get_data.lake.kinds import TABULAR, TEXTUAL, VIEWERS, extension
from basecast_get_data.lake.service import Lake, Target, TooLarge, get_lake
from basecast_get_data.lake.sources import meta_for
from basecast_get_data.lake.storage import PUBLIC_LAYERS, expires_at
from basecast_get_data.schemas.common import RowsPage, RowsResponse, meta
from basecast_get_data.schemas.lake import (
    DatasetRef,
    FormatCount,
    LakeFolder,
    LakeListing,
    LakeObject,
    LakeTotals,
    ListingResponse,
    ObjectDetail,
    ObjectResponse,
    ObjectStructure,
    ObjectText,
    ProcessedRef,
    RunRef,
    SignedUrl,
    SignedUrlResponse,
    SnapshotRef,
    SourceRef,
    SourcesData,
    SourcesResponse,
    SourceSummary,
    StructureResponse,
    TextResponse,
)
from basecast_get_data.schemas.tables import TableSummary
from basecast_get_data.tables import catalog, registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/lake", tags=["lake"], dependencies=[Depends(require_token)])

MAX_LIMIT = 1000
SOURCE_PREFIX = re.compile(r"^raw/source=([^/]+)/$")
DT_PREFIX = re.compile(r"^raw/source=([^/]+)/dt=([^/]+)/$")


def _out(o: RawObject) -> LakeObject:
    return LakeObject(
        key=o.key,
        source_id=o.source_id,
        dt=o.dt,
        file=o.file,
        kind=o.kind,  # type: ignore[arg-type]
        bytes=o.bytes,
        sha256=o.sha256,
        fetched_at=o.fetched_at,
        url=o.url,
        source_page=o.source_page,
        content_type=o.content_type,
        http_status=o.http_status,
        doc_id=o.doc_id,
        report_type_id=o.report_type_id,
        etag=o.etag,
        last_modified=o.last_modified,
        meta=o.meta,
    )


def _dataset_refs(source_id: str, tables: dict[str, TableSummary]) -> list[DatasetRef]:
    out = []
    for d in registry.by_source().get(source_id, []):
        t = tables.get(d.name)
        out.append(
            DatasetRef(
                name=d.name,
                target="bigquery" if d.target == "bigquery" else "postgres",
                mode=d.mode,
                loaded=bool(t and t.loaded),
                rows=t.rows if t else None,
                rows_estimated=bool(t and t.rows_estimated),
            )
        )
    return out


def _latest_runs() -> dict[str, list[RunRef]]:
    if not pg.db_available():
        return {}
    try:
        rows = pg.fetch_all(
            "SELECT DISTINCT ON (source, stage) source, stage, status, started_at, finished_at"
            " FROM etl_run ORDER BY source, stage, started_at DESC"
        )
    except Exception:
        log.exception("etl_run is not readable")
        return {}
    out: dict[str, list[RunRef]] = {}
    for r in rows:
        out.setdefault(r["source"], []).append(
            RunRef(
                stage=r["stage"], status=r["status"], started_at=r["started_at"], finished_at=r["finished_at"]
            )
        )
    return out


@router.get("/sources", response_model=SourcesResponse)
def lake_sources() -> SourcesResponse:
    lake = get_lake()
    snap = lake.index.snapshot()
    tables = {t.name: t for t in catalog.summaries()}
    runs = _latest_runs()
    items = []
    for source_id, objs in sorted(snap.by_source.items()):
        m = meta_for(source_id)
        dts = sorted({o.dt for o in objs})
        fetched = [o.fetched_at for o in objs if o.fetched_at]
        formats = Counter(extension(o.file) or "(none)" for o in objs)
        items.append(
            SourceSummary(
                source_id=source_id,
                name=m.name,
                group=m.group,
                publisher=m.publisher,
                description=m.description,
                upstream_url=m.upstream_url,
                catalog_id=m.catalog_id,
                schedule=m.schedule,
                schedule_cron=m.cron,
                files=len(objs),
                bytes=sum(o.bytes for o in objs),
                snapshots=len(dts),
                first_dt=dts[0] if dts else None,
                last_dt=dts[-1] if dts else None,
                last_fetched_at=max(fetched) if fetched else None,
                formats=[FormatCount(extension=e, files=n) for e, n in formats.most_common()],
                datasets=_dataset_refs(source_id, tables),
                last_runs=runs.get(source_id, []),
            )
        )
    data = SourcesData(
        bucket=lake.storage.uri,
        index_built_at=snap.built_at,
        totals=LakeTotals(
            sources=len(items), files=sum(i.files for i in items), bytes=sum(i.bytes for i in items)
        ),
        items=items,
    )
    return SourcesResponse(meta=meta(data_as_of=snap.built_at.date().isoformat()), data=data)


def _folder(name: str, prefix: str, objs: list[RawObject]) -> LakeFolder:
    fetched = [o.fetched_at for o in objs if o.fetched_at]
    return LakeFolder(
        name=name,
        prefix=prefix,
        files=len(objs),
        bytes=sum(o.bytes for o in objs),
        last_fetched_at=max(fetched) if fetched else None,
    )


@router.get("/list", response_model=ListingResponse)
def lake_list(
    prefix: str = Query("", description="Folder to list, ending in '/', or '' for the bucket root"),
    q: str = Query("", description="Keep only names containing this text (case-insensitive)"),
    recursive: bool = Query(False, description="Under raw/, list every object below the prefix"),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=MAX_LIMIT),
) -> ListingResponse:
    lake = get_lake()
    if prefix and not prefix.endswith("/"):
        raise HTTPException(422, "prefix must end with '/'")
    if ".." in prefix.split("/"):
        raise HTTPException(422, "invalid prefix")
    if prefix and not prefix.startswith(PUBLIC_LAYERS):
        raise HTTPException(404, f"no such folder: {prefix}")
    snap = lake.index.snapshot()
    needle = q.strip().lower()
    folders: list[LakeFolder] = []
    objects: list[LakeObject] = []

    if prefix.startswith(RAW) and recursive:
        objects = [_out(o) for o in snap.objects if o.key.startswith(prefix)]
    elif prefix == RAW:
        folders = [
            _folder(f"source={s}", f"raw/source={s}/", objs) for s, objs in sorted(snap.by_source.items())
        ]
    elif m := SOURCE_PREFIX.match(prefix):
        by_dt: dict[str, list[RawObject]] = {}
        for o in snap.by_source.get(m.group(1), []):
            by_dt.setdefault(o.dt, []).append(o)
        folders = [
            _folder(f"dt={d}", f"{prefix}dt={d}/", objs) for d, objs in sorted(by_dt.items(), reverse=True)
        ]
    elif m := DT_PREFIX.match(prefix):
        objects = [_out(o) for o in snap.by_source.get(m.group(1), []) if o.dt == m.group(2)]
    elif prefix.startswith(RAW):
        raise HTTPException(404, f"no such folder: {prefix}")
    else:
        names, infos = lake.storage.list_dir(prefix)
        if not prefix:
            names = [p for p in names if p in PUBLIC_LAYERS]
            infos = []
        for p in names:
            if p == "raw/":
                folders.append(_folder("raw", "raw/", snap.objects))
            else:
                folders.append(LakeFolder(name=p[len(prefix) :].rstrip("/"), prefix=p))
        for info in infos:
            parts = dict(x.split("=", 1) for x in info.key.split("/")[:-1] if "=" in x)
            objects.append(
                LakeObject(
                    key=info.key,
                    source_id=parts.get("source", info.key.split("/")[0]),
                    dt=parts.get("dt", ""),
                    file=info.key.rsplit("/", 1)[-1],
                    kind=readers.kind_of(info.key),  # type: ignore[arg-type]
                    bytes=info.size,
                )
            )

    if needle:
        folders = [f for f in folders if needle in f.name.lower()]
        objects = [o for o in objects if needle in o.file.lower()]
    total = len(objects)
    listing = LakeListing(
        prefix=prefix,
        folders=folders,
        objects=objects[offset : offset + limit],
        total_objects=total,
        offset=offset,
        limit=limit,
    )
    return ListingResponse(meta=meta(data_as_of=snap.built_at.date().isoformat()), data=listing)


def _object(lake: Lake, key: str) -> RawObject:
    try:
        return lake.object(key)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"no such object: {key}") from e


def _family(o: RawObject) -> str:
    """What makes two files "the same file" in different snapshots: the manifest's family when the source
    records one, else the name with its digits masked (GIS_Report_July2026 ~ GIS_Report_May2026)."""
    fam = o.meta.get("family") if isinstance(o.meta, dict) else None
    if fam:
        return f"family:{fam}"
    stem = re.sub(r"^RPT\.[0-9.]+\.", "", o.file)
    stem = re.sub(
        r"(January|February|March|April|May|June|July|August|September|October|November|December"
        r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)",
        "#",
        stem,
        flags=re.I,
    )
    return "name:" + re.sub(r"\d+", "#", stem)


@router.get("/object", response_model=ObjectResponse)
def lake_object(
    key: str = Query(..., description="Object key, e.g. raw/source=ercot_gis/dt=2026-08-01/<file>"),
):
    lake = get_lake()
    obj = _object(lake, key)
    tables = {t.name: t for t in catalog.summaries()}
    processed: list[ProcessedRef] = []
    if pg.db_available() and obj.key.startswith(RAW):
        try:
            rows = pg.fetch_all(
                "SELECT dataset, version, rows, processed_at, sha256 FROM lake_processed"
                " WHERE raw_key = %s ORDER BY dataset",
                (obj.key,),
            )
            processed = [
                ProcessedRef(
                    dataset=r["dataset"],
                    version=r["version"],
                    rows=r["rows"],
                    processed_at=r["processed_at"],
                    current=r["sha256"] == obj.sha256,
                )
                for r in rows
            ]
        except Exception:
            log.exception("lake_processed is not readable")
    family = _family(obj)
    siblings = [
        o
        for o in lake.index.snapshot().by_source.get(obj.source_id, [])
        if o.key != obj.key and o.dt != obj.dt and _family(o) == family
    ]
    siblings.sort(key=lambda o: o.dt, reverse=True)
    m = meta_for(obj.source_id)
    detail = ObjectDetail(
        object=_out(obj),
        source=SourceRef(
            source_id=obj.source_id, name=m.name, publisher=m.publisher, upstream_url=m.upstream_url
        ),
        viewer=VIEWERS.get(obj.kind, "download"),
        previewable=obj.kind != "other" and obj.bytes <= lake.preview_max_bytes,
        datasets=_dataset_refs(obj.source_id, tables),
        processed=processed,
        other_snapshots=[SnapshotRef(dt=o.dt, key=o.key, file=o.file, bytes=o.bytes) for o in siblings[:36]],
    )
    return ObjectResponse(meta=meta(sources=[obj.source_id]), data=detail)


def _target(lake: Lake, key: str, member: str | None) -> Target:
    obj = _object(lake, key)
    if member and obj.kind != "zip":
        raise HTTPException(422, "member only applies to zip files")
    return Target(obj, member or None)


def _guard(fn):
    """Maps parser failures to HTTP errors the app can show."""
    try:
        return fn()
    except TooLarge as e:
        raise HTTPException(413, "This file is too large to preview here; download it instead.") from e
    except readers.NotTabular as e:
        raise HTTPException(422, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, f"not found in this file: {e.args[0]}") from e
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        log.exception("could not read %s", fn)
        raise HTTPException(422, f"could not read this file: {e}") from e


@router.get("/object/structure", response_model=StructureResponse)
def lake_structure(key: str = Query(...), member: str | None = Query(None)) -> StructureResponse:
    lake = get_lake()
    target = _target(lake, key, member)
    kind = target.kind
    out = ObjectStructure(kind=kind, viewer=VIEWERS.get(kind, "download"))  # type: ignore[arg-type]
    if target.obj.bytes > lake.preview_max_bytes:
        out.too_large = True
        out.note = "This file is too large to preview here; download it instead."
        return StructureResponse(meta=meta(sources=[target.obj.source_id]), data=out)

    def fill() -> None:
        if kind == "zip":
            out.members = readers.zip_members(lake.local_file(target))
        elif kind == "sheet":
            out.sheets = lake.sheets(target)
        elif kind in ("json", "geojson"):
            out.json_tree = readers.read_json_tree(lake.local_file(target))
            try:
                parsed = lake.tabular(target, None)
            except readers.NotTabular:
                return
            out.columns, out.row_count, out.tabular_path = (
                parsed.columns,
                parsed.frame.height,
                parsed.tabular_path,
            )
        elif kind in ("text", "parquet"):
            parsed = lake.tabular(target, None)
            out.columns, out.row_count, out.delimiter = parsed.columns, parsed.frame.height, parsed.delimiter

    _guard(fill)
    return StructureResponse(meta=meta(sources=[target.obj.source_id]), data=out)


@router.get("/object/rows", response_model=RowsResponse)
def lake_rows(
    key: str = Query(...),
    member: str | None = Query(None, description="A file inside a zip"),
    sheet: str | None = Query(None, description="Spreadsheet sheet; the first one by default"),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=MAX_LIMIT),
    with_summary: bool = Query(False, description="Also return the total row count"),
) -> RowsResponse:
    lake = get_lake()
    target = _target(lake, key, member)
    if target.kind not in TABULAR:
        raise HTTPException(422, f"no grid view for {target.kind} files")
    parsed = _guard(lambda: lake.tabular(target, sheet))
    page = RowsPage(
        columns=parsed.columns,
        rows=parsed.rows(offset, limit),
        offset=offset,
        limit=limit,
        total=parsed.frame.height if with_summary else None,
    )
    return RowsResponse(meta=meta(sources=[target.obj.source_id]), data=page)


@router.get("/object/text", response_model=TextResponse)
def lake_text(key: str = Query(...), member: str | None = Query(None)) -> TextResponse:
    lake = get_lake()
    target = _target(lake, key, member)
    if target.kind not in TEXTUAL:
        raise HTTPException(422, f"no text view for {target.kind} files")
    blocks = _guard(lambda: lake.text(target))
    return TextResponse(
        meta=meta(sources=[target.obj.source_id]), data=ObjectText(kind=target.kind, blocks=blocks)
    )  # type: ignore[arg-type]


@router.get("/object/url", response_model=SignedUrlResponse)
def lake_signed_url(key: str = Query(...)) -> SignedUrlResponse:
    lake = get_lake()
    obj = _object(lake, key)
    ttl = timedelta(minutes=10)
    url = lake.storage.signed_url(obj.key, ttl)
    data = SignedUrl(url=url, expires_at=expires_at(ttl) if url else None)
    return SignedUrlResponse(meta=meta(sources=[obj.source_id]), data=data)


RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@router.get(
    "/object/content",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/octet-stream": {}}}, 206: {"description": "Partial content"}},
)
def lake_content(
    key: str = Query(...),
    member: str | None = Query(None),
    range_header: str | None = Header(None, alias="Range"),
) -> StreamingResponse:
    """The file's bytes, with HTTP Range support (pdf.js reads PDFs in ranges)."""
    lake = get_lake()
    target = _target(lake, key, member)
    if member:
        path = _guard(lambda: lake.local_file(target))
        size = path.stat().st_size

        def chunks(start: int, end: int):
            with path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(1 << 20, left))):
                    left -= len(chunk)
                    yield chunk
    else:
        size = target.obj.bytes or lake.storage.size(target.obj.key)

        def chunks(start: int, end: int):
            return lake.storage.iter_range(target.obj.key, start, end)

    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Encoding": "identity",
        "Content-Disposition": f'inline; filename="{target.name}"',
        "Cache-Control": "private, max-age=3600",
    }
    start, end, status = 0, size - 1, 200
    if range_header and (m := RANGE.match(range_header.strip())):
        a, b = m.groups()
        if a:
            start, end = int(a), min(int(b), size - 1) if b else size - 1
        elif b:
            start, end = max(0, size - int(b)), size - 1
        if start > end or start >= size:
            raise HTTPException(416, "range not satisfiable", headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(chunks(start, end), status_code=status, media_type=ctype, headers=headers)
