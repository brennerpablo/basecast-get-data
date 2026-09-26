GIS_DIR = "raw/source=ercot_gis/dt=2026-08-01/"
GIS = GIS_DIR + "RPT.00015933.0000000000000000.20260901.143805843.GIS_Report_August2026.xlsx"
ZIP = (
    "raw/source=ercot_load_wz_daily/dt=2026-09-25/"
    "cdr.00013101.0000000000000000.20260925.055000363.ACTUALSYSLOADWZNP6345_csv.zip"
)


def test_token_is_required(client):
    assert client.get("/health", headers={"Authorization": ""}).status_code == 200
    assert client.get("/lake/sources", headers={"Authorization": ""}).status_code == 401
    assert client.get("/lake/sources", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_sources(client):
    body = client.get("/lake/sources").json()
    assert body["meta"]["simulated"] is False
    data = body["data"]
    assert data["totals"]["sources"] == 6
    gis = next(s for s in data["items"] if s["source_id"] == "ercot_gis")
    assert gis["name"] == "GIS interconnection queue"
    assert gis["schedule"] == "Mondays 07:00 CT"
    assert (gis["files"], gis["snapshots"], gis["first_dt"], gis["last_dt"]) == (
        2,
        2,
        "2026-07-01",
        "2026-08-01",
    )
    assert gis["formats"] == [{"extension": "xlsx", "files": 2}]


def test_root_lists_only_public_layers(client):
    data = client.get("/lake/list", params={"prefix": ""}).json()["data"]
    assert [f["name"] for f in data["folders"]] == ["raw"]
    assert data["objects"] == []
    assert client.get("/lake/list", params={"prefix": "backups/"}).status_code == 404
    assert client.get("/lake/list", params={"prefix": "_logs/"}).status_code == 404


def test_private_and_escaping_keys_are_refused(client):
    assert client.get("/lake/object", params={"key": "backups/basecast.dump"}).status_code == 422
    assert (
        client.get("/lake/object/content", params={"key": "_logs/ercot_gis-20260926.log"}).status_code == 422
    )
    assert client.get("/lake/object", params={"key": "raw/../backups/basecast.dump"}).status_code == 422


def test_folders_by_level(client):
    raw = client.get("/lake/list", params={"prefix": "raw/"}).json()["data"]
    assert "source=ercot_gis" in [f["name"] for f in raw["folders"]]
    source = client.get("/lake/list", params={"prefix": "raw/source=ercot_gis/"}).json()["data"]
    assert [f["name"] for f in source["folders"]] == ["dt=2026-08-01", "dt=2026-07-01"]
    files = client.get("/lake/list", params={"prefix": GIS_DIR}).json()["data"]
    assert files["total_objects"] == 1 and files["objects"][0]["kind"] == "sheet"
    flat = client.get(
        "/lake/list", params={"prefix": "raw/source=ercot_gis/", "recursive": True, "q": "july"}
    )
    assert [o["dt"] for o in flat.json()["data"]["objects"]] == ["2026-07-01"]


def test_object_detail_links_other_months(client):
    data = client.get("/lake/object", params={"key": GIS}).json()["data"]
    assert data["viewer"] == "sheet_grid" and data["previewable"]
    assert data["object"]["meta"] == {"family": "gis_report"}
    assert [s["dt"] for s in data["other_snapshots"]] == ["2026-07-01"]
    assert client.get("/lake/object", params={"key": GIS_DIR + "missing.xlsx"}).status_code == 404


def test_sheet_structure_and_rows(client):
    sheets = client.get("/lake/object/structure", params={"key": GIS}).json()["data"]["sheets"]
    assert sheets[1] == {"name": "Project Details - Large Gen", "rows": 35, "columns": 12}
    page = client.get(
        "/lake/object/rows",
        params={
            "key": GIS,
            "sheet": "Project Details - Large Gen",
            "offset": 24,
            "limit": 2,
            "with_summary": True,
        },
    ).json()["data"]
    assert page["total"] == 35 and page["offset"] == 24
    assert page["rows"][0][0] == "INR"
    assert client.get("/lake/object/rows", params={"key": GIS, "sheet": "Nope"}).status_code == 404


def test_zip_members_and_member_rows(client):
    members = client.get("/lake/object/structure", params={"key": ZIP}).json()["data"]["members"]
    member = members[0]["name"]
    page = client.get("/lake/object/rows", params={"key": ZIP, "member": member, "limit": 1}).json()["data"]
    assert page["rows"][0][2] == "COAST"
    assert client.get("/lake/object/rows", params={"key": GIS, "member": "x"}).status_code == 422


def test_text_view(client):
    key = "raw/source=ercot_large_load_decks/dt=2026-06-19/June-19-LLWG-Report.pptx"
    blocks = client.get("/lake/object/text", params={"key": key}).json()["data"]["blocks"]
    assert len(blocks) == 2
    assert client.get("/lake/object/text", params={"key": GIS}).status_code == 422


def test_content_ranges(client):
    whole = client.get("/lake/object/content", params={"key": GIS})
    assert whole.status_code == 200 and whole.content[:2] == b"PK"
    part = client.get("/lake/object/content", params={"key": GIS}, headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == b"PK\x03\x04"
    assert part.headers["content-range"].startswith("bytes 0-3/")
    too_far = client.get("/lake/object/content", params={"key": GIS}, headers={"Range": "bytes=99999999-"})
    assert too_far.status_code == 416


def test_signed_url_is_null_on_local_storage(client):
    assert client.get("/lake/object/url", params={"key": GIS}).json()["data"] == {
        "url": None,
        "expires_at": None,
    }


def test_tables_without_a_database(client):
    assert client.get("/tables").json()["data"]["items"] == []
    assert client.get("/tables/open_meteo_hourly/rows").status_code == 404
    assert client.get("/pipeline/runs").status_code == 503
