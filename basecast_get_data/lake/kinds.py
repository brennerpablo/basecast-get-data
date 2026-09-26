"""File kinds by extension, and the viewer the app opens for each."""

from __future__ import annotations

KINDS = {
    "xlsx": "sheet",
    "xlsm": "sheet",
    "xls": "sheet",
    "xlsb": "sheet",
    "ods": "sheet",
    "csv": "text",
    "txt": "text",
    "dat": "text",
    "tsv": "text",
    "parquet": "parquet",
    "json": "json",
    "geojson": "geojson",
    "zip": "zip",
    "pdf": "pdf",
    "pptx": "slides",
    "docx": "document",
    "html": "html",
    "htm": "html",
}

VIEWERS = {
    "sheet": "sheet_grid",
    "text": "text_grid",
    "parquet": "typed_grid",
    "json": "json",
    "geojson": "json",
    "zip": "zip_members",
    "pdf": "pdf",
    "slides": "text_blocks",
    "document": "text_blocks",
    "html": "text_blocks",
    "other": "download",
}

# Kinds whose rows come from /lake/object/rows.
TABULAR = {"sheet", "text", "parquet", "json", "geojson"}
# Kinds whose content comes from /lake/object/text.
TEXTUAL = {"slides", "document", "html"}


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_of(name: str) -> str:
    return KINDS.get(extension(name), "other")
