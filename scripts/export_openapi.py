"""Writes openapi.json, the contract basecast-app generates its client from.

    uv run python scripts/export_openapi.py
tests/test_openapi.py fails while the file is stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from basecast_get_data.main import create_app

SPEC = Path(__file__).resolve().parent.parent / "openapi.json"

if __name__ == "__main__":
    SPEC.write_text(json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {SPEC.name}")
