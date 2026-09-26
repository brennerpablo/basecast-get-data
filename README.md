# basecast-get-data

FastAPI service for **basecast**, which forecasts how much of ERCOT's interconnection queues (large loads
and generation) actually gets built, where and when, and turns that into peak-demand forecasts by region.

It serves `basecast-app` through one typed endpoint per resource and owns the data contract between the
pipelines and the frontend. The OpenAPI spec generates the app's TypeScript client.

> **Status:** contract v2 is live. The accounts, Explorer, forecast and backtest resources read the marts
> basecast-airflow writes to `public.mart_*`, group by group (`DATA_MODE`, `MARTS_LIVE`); a group not switched
> yet serves invented fixtures (`simulated: true`, caveat `fixture`). The /data browser's endpoints read the
> real lake, tables and pipeline runs.

## Repos

| Repo | Role |
|---|---|
| `basecast-airflow` | Ingestion and models |
| `basecast-get-data` | FastAPI service; owns the data contract (this repo) |
| `basecast-app` | Next.js frontend |

## Setup

```bash
cp .env.example .env    # API_TOKEN, the local lake and Postgres; never commit .env
uv sync
cloud-sql-proxy --port 5439 --quota-project basecast-509812 basecast-509812:us-central1:basecast-pg
uv run uvicorn basecast_get_data.main:app --reload --port 8000   # docs at http://localhost:8000/docs
```

Production settings are the defaults in `basecast_get_data/config.py`; locally `.env` points the lake at
`../basecast-airflow/data` and Postgres at the proxy. BigQuery uses your application default credentials.
Every route but `/health` needs `Authorization: Bearer $API_TOKEN`.

## Endpoints

| Route | What |
|---|---|
| `GET /accounts` · `/accounts/export.csv` | Ranked co-ops and munis, with filters; the same rows as CSV |
| `GET /accounts/{id}` · `/accounts/{id}/events` | One account's diagnosis (facts with source and as-of); its event history |
| `GET /geo/counties` · `/geo/counties/{fips}` | The 254 counties for the map (acquisition, generation queue, data centers); one county |
| `GET /queue/projects` | Generation-queue projects with their chance of reaching COD |
| `GET /forecasts/peak` · `/forecasts/large-load` | Peak forecast in three layers vs ERCOT's; the large-load flow |
| `GET /backtest/peak` · `/official-errors` · `/queue` | Our model and ERCOT's forecasts against the actual; the queue model |
| `GET /caveats` · `/glossary` | The caveat texts; the labels of the trigger, flag and next-action codes |
| `GET /lake/sources` | Every raw source: files, snapshots, formats, datasets, last runs |
| `GET /lake/list` | Folders and files under a prefix of the lake |
| `GET /lake/object` | One file: manifest entry, lineage, the same file in other snapshots |
| `GET /lake/object/structure` · `/rows` · `/text` | Previews: sheets, zip members, blocks of rows, slide text |
| `GET /lake/object/url` · `/content` | A signed URL, or the bytes with HTTP Range |
| `GET /tables` · `/tables/{name}` | Declared and loaded tables in Postgres and BigQuery, with columns |
| `GET /tables/{name}/rows` · `/lineage` | Blocks of rows (sort, filters, totals) and the raw files behind them |
| `GET /pipeline/runs` | `etl_run` history |

`docs/data-contract.md` has the rules (envelope, caveats, errors, which mart feeds each resource, and for
/data pagination, filters and what is browsable); `openapi.json` the exact shapes.

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                            # against tests/fixtures/lake, trimmed from real files
uv run python scripts/export_openapi.py  # after any change to a route or model; a test fails if stale
uv run python scripts/make_contract_fixtures.py  # after a change to a mart's columns (data/fixtures)
```

A push to `main` deploys to Cloud Run (`.github/workflows/deploy.yml`), so the checks pass first. After
exporting `openapi.json`, regenerate the app's client (`npm run api:generate` in basecast-app).

## Docs

- `docs/KICKOFF.md`: project kickoff (Portuguese)
- `docs/data-contract.md`: the data contract (v2)
- `CLAUDE.md`: working context for Claude Code
