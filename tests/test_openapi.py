import json
from pathlib import Path

from basecast_get_data.main import create_app

SPEC = Path(__file__).resolve().parent.parent / "openapi.json"


def test_openapi_json_is_up_to_date():
    """The app generates its client from openapi.json: run `uv run python scripts/export_openapi.py`."""
    assert json.loads(SPEC.read_text()) == create_app().openapi()
