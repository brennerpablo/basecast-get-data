"""The lake browser: sources, folders, raw objects and their previews."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from basecast_get_data.schemas.common import GridColumn, Meta

FileKind = Literal[
    "sheet", "text", "parquet", "json", "geojson", "zip", "pdf", "slides", "document", "html", "other"
]


class FormatCount(BaseModel):
    extension: str
    files: int


class DatasetRef(BaseModel):
    name: str
    target: Literal["postgres", "bigquery"]
    mode: str
    loaded: bool
    rows: int | None = None
    rows_estimated: bool = False


class RunRef(BaseModel):
    stage: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None


class SourceSummary(BaseModel):
    source_id: str
    name: str
    group: str
    publisher: str | None = None
    description: str | None = None
    upstream_url: str | None = None
    catalog_id: str | None = None
    schedule: str | None = None
    schedule_cron: str | None = None
    files: int
    bytes: int
    snapshots: int
    first_dt: str | None = None
    last_dt: str | None = None
    last_fetched_at: datetime | None = None
    formats: list[FormatCount]
    datasets: list[DatasetRef]
    last_runs: list[RunRef]


class LakeTotals(BaseModel):
    sources: int
    files: int
    bytes: int


class SourcesData(BaseModel):
    bucket: str
    index_built_at: datetime
    totals: LakeTotals
    items: list[SourceSummary]


class SourcesResponse(BaseModel):
    meta: Meta
    data: SourcesData


class LakeObject(BaseModel):
    key: str
    source_id: str
    dt: str
    file: str
    kind: FileKind
    bytes: int
    sha256: str | None = None
    fetched_at: datetime | None = None
    url: str | None = None
    source_page: str | None = None
    content_type: str | None = None
    http_status: int | None = None
    doc_id: str | None = None
    report_type_id: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    meta: dict[str, Any] = {}


class LakeFolder(BaseModel):
    name: str
    prefix: str
    # None outside raw/, where only the manifests are indexed.
    files: int | None = None
    bytes: int | None = None
    last_fetched_at: datetime | None = None


class LakeListing(BaseModel):
    prefix: str
    folders: list[LakeFolder]
    objects: list[LakeObject]
    total_objects: int
    offset: int
    limit: int


class ListingResponse(BaseModel):
    meta: Meta
    data: LakeListing


class ProcessedRef(BaseModel):
    dataset: str
    version: int
    rows: int | None = None
    processed_at: datetime
    # False when the file changed after it was processed (another sha256 under the same key).
    current: bool


class SnapshotRef(BaseModel):
    dt: str
    key: str
    file: str
    bytes: int


class SourceRef(BaseModel):
    source_id: str
    name: str
    publisher: str | None = None
    upstream_url: str | None = None


class ObjectDetail(BaseModel):
    object: LakeObject
    source: SourceRef
    viewer: str
    previewable: bool
    datasets: list[DatasetRef]
    processed: list[ProcessedRef]
    other_snapshots: list[SnapshotRef]


class ObjectResponse(BaseModel):
    meta: Meta
    data: ObjectDetail


class SheetInfo(BaseModel):
    name: str
    # None until the sheet is parsed (big workbooks are parsed one sheet at a time).
    rows: int | None = None
    columns: int | None = None


class ZipMember(BaseModel):
    name: str
    kind: FileKind
    bytes: int
    compressed_bytes: int
    modified: str | None = None


class JsonNode(BaseModel):
    key: str | None = None
    type: Literal["object", "array", "string", "number", "boolean", "null"]
    value: str | None = None
    length: int | None = None
    children: list[JsonNode] | None = None


class ObjectStructure(BaseModel):
    kind: FileKind
    viewer: str
    too_large: bool = False
    note: str | None = None
    sheets: list[SheetInfo] | None = None
    members: list[ZipMember] | None = None
    columns: list[GridColumn] | None = None
    row_count: int | None = None
    delimiter: str | None = None
    tabular_path: str | None = None
    json_tree: JsonNode | None = None


class StructureResponse(BaseModel):
    meta: Meta
    data: ObjectStructure


class TextBlock(BaseModel):
    title: str | None = None
    text: str


class ObjectText(BaseModel):
    kind: FileKind
    blocks: list[TextBlock]


class TextResponse(BaseModel):
    meta: Meta
    data: ObjectText


class SignedUrl(BaseModel):
    # None when this deployment cannot sign URLs; the caller then streams /lake/object/content.
    url: str | None
    expires_at: datetime | None = None


class SignedUrlResponse(BaseModel):
    meta: Meta
    data: SignedUrl
