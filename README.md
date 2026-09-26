# basecast-get-data

FastAPI service for **basecast**, which forecasts how much of ERCOT's interconnection queues (large loads
and generation) actually gets built, where and when, and turns that into peak-demand forecasts by region.

It serves `basecast-app` through one typed endpoint per resource and owns the data contract between the
pipelines and the frontend. The OpenAPI spec generates the app's TypeScript client.

> **Status:** bootstrap. The contract (`docs/data-contract.md`) is a draft; endpoints backed by fixtures
> labeled as simulated come next (tasks C0 and C1).

## Repos

| Repo | Role |
|---|---|
| `basecast-airflow` | Ingestion and models |
| `basecast-get-data` | FastAPI service; owns the data contract (this repo) |
| `basecast-app` | Next.js frontend |

## Setup

```bash
cp .env.example .env   # API_TOKEN, DATA_MODE=fixtures; never commit .env
```

## Docs

- `docs/KICKOFF.md`: project kickoff (Portuguese)
- `docs/data-contract.md`: contract draft
- `CLAUDE.md`: working context for Claude Code
