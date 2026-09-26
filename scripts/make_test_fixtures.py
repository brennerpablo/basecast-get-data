"""Trims real lake files into the small test lake under tests/fixtures/lake.

Run from the repo root with basecast-airflow's local lake next to this repo:
    uv run python scripts/make_test_fixtures.py
The output is committed; rerun only to refresh the fixtures.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

import fastexcel
import orjson
import polars as pl
import xlsxwriter
from pptx import Presentation

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "basecast-airflow" / "data" / "raw"
OUT = ROOT / "tests" / "fixtures" / "lake"


def write_manifest(folder: Path, source: str, day: str, extra: dict[str, dict]) -> None:
    entries = []
    for f in sorted(folder.iterdir()):
        if f.name == "_manifest.json":
            continue
        data = f.read_bytes()
        entries.append(
            {
                "file": f.name,
                "url": extra.get(f.name, {}).get("url", f"https://example.test/{f.name}"),
                "fetched_at": "2026-09-26T04:29:28.935693+00:00",
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "http_status": 200,
                "source_page": extra.get(f.name, {}).get("source_page"),
                "doc_id": None,
                "report_type_id": None,
                "content_type": None,
                "etag": None,
                "last_modified": None,
                "meta": extra.get(f.name, {}).get("meta", {}),
            }
        )
    (folder / "_manifest.json").write_text(
        json.dumps({"source": source, "dt": day, "entries": entries}, indent=2)
    )


def folder(source: str, day: str) -> Path:
    path = OUT / "raw" / f"source={source}" / f"dt={day}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gis() -> None:
    """Two monthly GIS reports: the first 35 rows x 12 columns of "Project Details - Large Gen"."""
    for day, name in [
        ("2026-08-01", "RPT.00015933.0000000000000000.20260901.143805843.GIS_Report_August2026.xlsx"),
        ("2026-07-01", "RPT.00015933.0000000000000000.20260803.153950829.GIS_Report_July2026.xlsx"),
    ]:
        src = next((SRC / "source=ercot_gis" / f"dt={day}").glob("*GIS_Report*"))
        reader = fastexcel.read_excel(str(src))
        out = folder("ercot_gis", day)
        wb = xlsxwriter.Workbook(str(out / name))
        date_fmt = wb.add_format({"num_format": "m/d/yyyy"})
        for sheet, rows, cols in [("Contents", 6, 2), ("Project Details - Large Gen", 35, 12)]:
            frame = reader.load_sheet(sheet, header_row=None).to_polars()
            ws = wb.add_worksheet(sheet)
            for r in range(min(rows, frame.height)):
                for c, v in enumerate(frame.row(r)[:cols]):
                    if isinstance(v, dt.datetime):
                        ws.write_datetime(r, c, v, date_fmt)
                    elif v is not None:
                        ws.write(r, c, v)
        wb.close()
        write_manifest(out, "ercot_gis", day, {name: {"meta": {"family": "gis_report"}}})


def load_zip() -> None:
    """One real NP6-345-CD daily zip (1.4 KB, a CSV inside)."""
    src = next((SRC / "source=ercot_load_wz_daily" / "dt=2026-09-25").glob("*.zip"))
    out = folder("ercot_load_wz_daily", "2026-09-25")
    shutil.copy(src, out / src.name)
    write_manifest(out, "ercot_load_wz_daily", "2026-09-25", {})


def open_meteo() -> None:
    """east_tyler_2018.json cut to its first 48 hours."""
    doc = orjson.loads((SRC / "source=open_meteo" / "dt=2026-09-25" / "east_tyler_2018.json").read_bytes())
    doc["hourly"] = {k: v[:48] for k, v in doc["hourly"].items()}
    out = folder("open_meteo", "2026-09-25")
    (out / "east_tyler_2018.json").write_bytes(orjson.dumps(doc))
    write_manifest(out, "open_meteo", "2026-09-25", {})


def noaa() -> None:
    """The first 20 rows and 8 columns of a GHCNh station-year."""
    frame = pl.read_parquet(SRC / "source=noaa_ghcnh" / "dt=2026-09-26" / "GHCNh_USW00003927_2001.parquet")
    out = folder("noaa_ghcnh", "2026-09-26")
    frame.select(frame.columns[:8]).head(20).write_parquet(out / "GHCNh_USW00003927_2001.parquet")
    write_manifest(out, "noaa_ghcnh", "2026-09-26", {})


def census_txt() -> None:
    """The first 12 lines of a Census Building Permits county file."""
    src = sorted((SRC / "source=census_bps" / "dt=2026-09-25").glob("*.txt"))[0]
    out = folder("census_bps", "2026-09-25")
    lines = src.read_text(errors="replace").splitlines()[:12]
    (out / src.name).write_text("\n".join(lines) + "\n")
    write_manifest(out, "census_bps", "2026-09-25", {})


def deck() -> None:
    """A two-slide deck with the title and one line of the real June 19 LLWG report."""
    src = SRC / "source=ercot_large_load_decks" / "dt=2026-06-19" / "June-19-LLWG-Report.pptx"
    real = Presentation(str(src))
    prs = Presentation()
    for slide in list(real.slides)[:2]:
        title = slide.shapes.title.text if slide.shapes.title is not None else ""
        body = next(
            (s.text_frame.text for s in slide.shapes if s.has_text_frame and s != slide.shapes.title), ""
        )
        new = prs.slides.add_slide(prs.slide_layouts[1])
        new.shapes.title.text = title
        new.placeholders[1].text = body[:200]
    out = folder("ercot_large_load_decks", "2026-06-19")
    prs.save(str(out / "June-19-LLWG-Report.pptx"))
    write_manifest(out, "ercot_large_load_decks", "2026-06-19", {})


def private() -> None:
    """Folders the API must never expose."""
    (OUT / "_logs").mkdir(parents=True, exist_ok=True)
    (OUT / "_logs" / "ercot_gis-20260926.log").write_text("private\n")
    (OUT / "backups").mkdir(parents=True, exist_ok=True)
    (OUT / "backups" / "basecast.dump").write_text("private\n")


if __name__ == "__main__":
    shutil.rmtree(OUT, ignore_errors=True)
    gis()
    load_zip()
    open_meteo()
    noaa()
    census_txt()
    deck()
    private()
    print(f"wrote {sum(1 for p in OUT.rglob('*') if p.is_file())} files under {OUT.relative_to(ROOT)}")
