import datetime as dt
from pathlib import Path

import pytest

from basecast_get_data.lake import readers
from basecast_get_data.lake.index import parse_manifest

from .conftest import FIXTURES

RAW = FIXTURES / "raw"
GIS = next((RAW / "source=ercot_gis" / "dt=2026-08-01").glob("*.xlsx"))


def test_letters():
    assert [readers.letters(i) for i in (0, 25, 26, 51, 701, 702)] == ["A", "Z", "AA", "AZ", "ZZ", "AAA"]


def test_to_cell_positional_shows_what_a_spreadsheet_shows():
    assert readers.to_cell(203.0, positional=True) == 203
    assert readers.to_cell(203.0) == 203.0
    assert readers.to_cell(dt.datetime(2026, 12, 3), positional=True) == "2026-12-03"
    assert readers.to_cell("2026-12-03 00:00:00", positional=True) == "2026-12-03"
    assert readers.to_cell(float("nan")) is None
    assert readers.to_cell(2**60) == str(2**60)
    assert readers.to_cell({"a": 1}) == '{"a":1}'


def test_sheet_is_positional_and_keeps_the_publishers_layout():
    assert readers.sheet_names(GIS) == ["Contents", "Project Details - Large Gen"]
    parsed = readers.read_sheet(GIS, "Project Details - Large Gen")
    assert parsed.positional
    assert [c.name for c in parsed.columns][:3] == ["A", "B", "C"]
    rows = parsed.rows(24, 6)
    assert rows[0][:2] == ["INR", "Project Name"]  # the header sits on row 25
    assert rows[5][0] == "15INR0064b"
    assert rows[5][7] == "2026-12-03"  # Projected COD, a date cell


def test_sniff_delimiter():
    assert readers.sniff_delimiter("a|b|c\n1|2|3\n") == "|"
    assert readers.sniff_delimiter("a,b,c\n1,2,3\n") == ","
    assert readers.sniff_delimiter("just some words\nno fields here\n") is None


def test_census_text_file():
    path = next((RAW / "source=census_bps" / "dt=2026-09-25").glob("*.txt"))
    parsed = readers.read_text(path)
    assert parsed.delimiter == ","
    assert parsed.frame.height == 12
    assert parsed.rows(0, 1)[0][:2] == ["Survey", "FIPS"]


def test_zip_member_csv(tmp_path: Path):
    path = next((RAW / "source=ercot_load_wz_daily" / "dt=2026-09-25").glob("*.zip"))
    members = readers.zip_members(path)
    assert len(members) == 1 and members[0].kind == "text"
    csv = readers.extract_member(path, members[0].name, tmp_path / "m.csv")
    rows = readers.read_text(csv).rows(0, 2)
    assert rows[0][:3] == ["OperDay", "HourEnding", "COAST"]
    assert rows[1][:2] == ["09/24/2026", "01:00"]
    with pytest.raises(KeyError):
        readers.extract_member(path, "nope.csv", tmp_path / "x")


def test_json_parallel_arrays_become_a_table():
    path = RAW / "source=open_meteo" / "dt=2026-09-25" / "east_tyler_2018.json"
    parsed = readers.read_json(path)
    assert parsed.tabular_path == "hourly"
    assert [(c.name, c.type) for c in parsed.columns] == [
        ("time", "text"),
        ("temperature_2m", "number"),
        ("dew_point_2m", "number"),
    ]
    assert parsed.frame.height == 48
    assert parsed.rows(0, 1) == [["2018-01-01T00:00", 0.2, -3.6]]
    tree = readers.read_json_tree(path)
    hourly = next(c for c in tree.children if c.key == "hourly")
    time = next(c for c in hourly.children if c.key == "time")
    assert time.type == "array" and time.length == 48 and len(time.children) == 5


def test_geojson_properties_become_rows(tmp_path: Path):
    path = tmp_path / "t.geojson"
    path.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"NAME":"A","ID":1},'
        '"geometry":{"type":"Polygon","coordinates":[]}}]}'
    )
    parsed = readers.read_json(path)
    assert [c.name for c in parsed.columns] == ["NAME", "ID", "geometry"]
    assert parsed.rows(0, 1) == [["A", 1.0, "Polygon"]]


def test_parquet_keeps_names_and_types():
    path = RAW / "source=noaa_ghcnh" / "dt=2026-09-26" / "GHCNh_USW00003927_2001.parquet"
    parsed = readers.read_parquet(path)
    assert not parsed.positional
    assert parsed.columns[0].name == "STATION"
    assert parsed.frame.height == 20


def test_pptx_text_per_slide():
    path = RAW / "source=ercot_large_load_decks" / "dt=2026-06-19" / "June-19-LLWG-Report.pptx"
    blocks = readers.pptx_blocks(path)
    assert len(blocks) == 2
    assert blocks[0].title.startswith("Slide 1: Large Load Interconnection Status Update")
    assert "\x0b" not in blocks[0].title + blocks[0].text


def test_manifest_folder_comes_from_the_key():
    raw = b'{"source":"elsewhere","dt":"1999-01-01","entries":[{"file":"a.csv","bytes":3},{"file":"../x"}]}'
    objs = parse_manifest("raw/source=s1/dt=2026-01-02/_manifest.json", raw)
    assert [(o.key, o.source_id, o.dt) for o in objs] == [("raw/source=s1/dt=2026-01-02/a.csv", "s1", "2026-01-02")]
