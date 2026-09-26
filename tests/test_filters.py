import pytest

from basecast_get_data.lake.sources import describe_cron
from basecast_get_data.tables import postgres
from basecast_get_data.tables.filters import FilterError, like_pattern, parse_filters

COLS = [
    postgres.PgColumn("point_id", "text", "text", False, 1),
    postgres.PgColumn("ts_utc", "date", "timestamp with time zone", False, 2),
    postgres.PgColumn("temperature_c", "number", "double precision", True, 3),
    postgres.PgColumn("geom", "geometry", "geometry(Geometry,4326)", True, 4),
]
NAMES = {c.name for c in COLS}


def test_parse_filters():
    fs = parse_filters(
        ["point_id:eq:east_tyler", "ts_utc:between:2018-01-01\x1f2018-12-31", "geom:null:"], NAMES
    )
    assert [(f.column, f.op, f.values) for f in fs] == [
        ("point_id", "eq", ("east_tyler",)),
        ("ts_utc", "between", ("2018-01-01", "2018-12-31")),
        ("geom", "null", ("",)),
    ]
    # Values may hold colons.
    assert parse_filters(["point_id:eq:a:b"], NAMES)[0].value == "a:b"


@pytest.mark.parametrize(
    "raw",
    ["point_id", "nope:eq:1", "point_id:like:x", "point_id:eq:", "ts_utc:between:2018-01-01"],
)
def test_bad_filters(raw):
    with pytest.raises(FilterError):
        parse_filters([raw], NAMES)


def test_like_pattern_escapes_wildcards():
    assert like_pattern("50%_off", prefix_only=False) == "%50\\%\\_off%"
    assert like_pattern("ab", prefix_only=True) == "ab%"


def test_rows_query_quotes_identifiers_and_binds_values():
    filters = parse_filters(["point_id:starts:east", "temperature_c:gte:30", "point_id:in:a\x1fb"], NAMES)
    query, params = postgres.rows_query(
        "open_meteo_hourly", COLS, filters, "temperature_c", True, ["point_id", "ts_utc"]
    )
    text = query.as_string(None)
    assert 'FROM "public"."open_meteo_hourly"' in text
    assert '"point_id"::text ILIKE %s' in text
    assert '"temperature_c" >= %s' in text
    assert '"point_id"::text = ANY(%s)' in text
    assert 'ORDER BY "temperature_c" DESC NULLS LAST, "point_id", "ts_utc"' in text
    assert "GeometryType" in text
    assert params == ["east%", "30", ["a", "b"]]


def test_without_sort_or_key_there_is_no_order_by():
    query, _ = postgres.rows_query("t", COLS, [], None, False, [])
    assert "ORDER BY" not in query.as_string(None)


def test_describe_cron():
    assert describe_cron("0 7 * * 1") == "Mondays 07:00 CT"
    assert describe_cron("15 6 * * *") == "Daily 06:15 CT"
    assert describe_cron("0 9 10 * *") == "Monthly, day 10 09:00 CT"
    assert describe_cron("0 7 15 1,4,7,10 *") == "Quarterly, day 15 07:00 CT"
    assert describe_cron(None) == "Manual"
