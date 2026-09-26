# CLAUDE.md — basecast-get-data

**basecast** is being built for the Base Power × AITX Hackathon (Austin, Sep 25–27, 2026). This repo is
**front C: the API skeleton and the data contract**. It is small on purpose: a FastAPI service with one
typed endpoint per resource, serving labeled fixtures now and the marts later.

This file carries the stable parts of `docs/KICKOFF.md` (in Portuguese): sections 1, 2 and 7, this repo's
part of section 3, the working rules from section 0 and the front C conventions from section 6. The tasks
and their done criteria (C0–C2) stay only in `docs/KICKOFF.md` §6.

## Working agreements

- Code, comments, README and names in English. Talk to the user in Portuguese.
- Plan each piece of work and show the plan to the user; implement only after approval.
- Never use "Novi" in code, packages or branding: Novi Labs is a real Austin company, and
  "Novi for Energy" is only the pitch analogy.
- Anything not confirmed at the source stays marked "not verified".
- Log decisions in `docs/decisions.md`, one line each: date, decision, reason. This repo owns the
  contract, so decisions that affect more than one repo are logged here too.
- Work on `main` only, in all three repos: no feature branches, no worktrees. Small commits pushed straight
  to `main`. A push to `main` deploys to production (Cloud Run for this repo, once it has a Dockerfile),
  so the checks pass before every push.

## Product context (KICKOFF §1)

Base Power is an Austin energy company. It installs batteries in homes (and keeps owning them), sells
retail power and runs the fleet as a virtual power plant. It makes money from three sources: homeowners,
the ERCOT wholesale market, and utilities (co-ops and munis that buy capacity).

**Problem:** Texas plans its grid around inflated interconnection queues. In Jan 2026 ERCOT was tracking
~232.5 GW of large loads, only 3.8% of them approved to energize, against a demand record of 85,508 MW
(2023-08-10), which stood until 2026-07-22, when the peak reached 91.1 GW (preliminary). ERCOT's preliminary
long-term forecast for summer 2026 (~112 GW) missed that year's peak by about 21 GW.

**Product:** forecast how much of the queues (large loads and generation) actually gets built, where and
when; turn that into a **peak MW** forecast by region and year (P10/P50/P90); and translate it into
decisions for Base, mainly for the partnerships team: which co-ops to approach, when, and with what offer.

**Modules:**
1. **Explorer:** Texas map by county; raw vs. adjusted queue; priority acquisition zones.
2. **Forecast:** per-project survival in the generation queue; aggregate flow by stage for large loads;
   weather-normalized load time series; backtest.
3. **Commercial intelligence (core of the demo):** prioritized accounts, triggers, per-co-op diagnosis,
   rule-based next action. Read-only; CSV/webhook export.
4. **Private data adapters:** `FleetDataSource` (Base's fleet, simulated) and `UtilityDataSource`
   (large-load requests the co-op itself received). Public data gives a zone-level view; private data
   takes the diagnosis down to the territory.

**Judging:** 5-minute video + code. Completeness without crashes, technical depth, track fit (Open Grid
Data is the main track), non-obvious insight, usability, performance.

## Architecture decisions, closed (KICKOFF §2)

- **Three repos, same design as Fundsys:** `basecast-airflow` (ingestion and models), `basecast-get-data`
  (API) and `basecast-app` (frontend). No monorepo.
- **Now:** mining runs locally on the Mac mini (Apple Silicon). Later it moves to a personal GCP project
  in `us-central1` (the ERCOT API blocks access from outside the US) and a personal Vercel account.
- **Lake:** immutable raw `raw/source=<id>/dt=<snapshot date>/<original file>` plus typed Parquet
  `parquet/<dataset>/dt=<date>/part-*.parquet`. The local layout is identical to the GCS bucket's, so
  moving up is `gcloud storage rsync` plus a BigQuery load, with no code rewrite.
- **Warehouse (later):** BigQuery, tables partitioned by day. No Cloud SQL (there are no user writes).
- **Pipelines (`basecast-airflow`):** each source is a pure Python module with
  `run(*, storage, http, since=None, until=None)` that runs on its own from the CLI. The Airflow DAGs
  (later, on a VM with Docker Compose and LocalExecutor) will be thin and only call these `run()`
  functions. Patterns inherited from Fundsys: a `full` / `incremental` DAG factory, `etl_run` (one row per
  run with status, duration and events), idempotency (reprocessing never duplicates).
- **API (`basecast-get-data`):** FastAPI, later on Cloud Run. Same service name as Fundsys's, but
  **one typed endpoint per resource** (no dispatch by `process`), Pydantic, and an OpenAPI spec that
  generates the app's TypeScript client. Small marts loaded in memory (Polars) so the sliders respond in
  milliseconds.
- **Frontend (`basecast-app`):** Next.js on Vercel, built on the Fundsys base. The browser only talks to
  the BFF (route handlers); the API token stays on the server. TanStack Query. MapLibre for the map.
- **Time:** store everything in UTC; keep the sources' local hour and DST flag (ERCOT repeats an hour in
  November and uses "hour ending"); crons and display in `America/Chicago`.
- **Out of the MVP:** ancillary service prices (the post-RTC+B series is under a year old), 60-day
  disclosures, outages, short-term price forecasting, multi-tenancy, user writes.

## Repo layout (KICKOFF §3)

```
basecast-get-data/
├── CLAUDE.md
├── README.md
├── .env.example              # API_TOKEN, DATA_MODE; never commit .env
├── pyproject.toml            # Python 3.12, uv, FastAPI
├── basecast_get_data/
│   ├── main.py               # create_app(); config.py (env, production defaults), auth.py (Bearer)
│   ├── routers/              # one router per resource: accounts, geo, queue, forecasts, backtest, catalogs
│   │                         #   (/caveats, /glossary) (contract v2), lake, tables, pipeline (/data)
│   ├── schemas/              # Pydantic models = the contract; caveats.py is the caveat catalog
│   ├── products/             # marts.py (registry: mart → group, required columns), store.py (frames from
│   │                         #   fixtures or public.mart_*, DATA_MODE/MARTS_LIVE, refresh), envelope.py (meta)
│   ├── lake/                 # storage (local | GCS), manifest index, file readers, parse cache
│   ├── tables/               # dataset_registry, Postgres and BigQuery catalog and rows, filter grammar
│   ├── db/                   # Postgres pool (basecast_reader) and BigQuery client
│   └── data/                 # sources.yaml (raw source names); fixtures/<mart>.json (invented marts)
├── openapi.json              # exported on every change; the app generates its client from it
├── scripts/                  # export_openapi.py, make_test_fixtures.py, make_contract_fixtures.py
├── tests/                    # fixtures/lake: a small lake trimmed from real files
└── docs/
    ├── KICKOFF.md
    ├── data-contract.md      # KICKOFF §6; owner of the contract between data and frontend
    └── decisions.md          # this repo's decisions plus every cross-repo decision
```

## Front C conventions (KICKOFF §6)

- **Contract first.** `docs/data-contract.md` and the Pydantic models in `schemas/` are the contract
  between `basecast-airflow` (produces), this repo (serves) and `basecast-app` (consumes). The contract is
  still a draft: review it before writing the models.
- **Keys:** `county_fips` (5-digit string), `weather_zone` (`COAST`, `EAST`, `FWEST`, `NORTH`, `NCENT`,
  `SOUTH`, `SCENT`, `WEST`), `load_zone`, `utility_id` (EIA ID), `inr`; dates in ISO 8601 UTC; MW as
  float.
- **Resources:** county metrics (Explorer), forecast series with P10/P50/P90, backtest series (official,
  adjusted, actual, our model), account list and detail (triggers, deficit by year, next action with
  evidence) and pipeline runs.
- **Endpoints:** one router per resource, returning realistic JSON fixtures labeled as simulated. Export
  `openapi.json` on every change (a script, or a test that fails when it is stale). Simple Bearer token
  auth.
- **Later (out of scope now):** replace the fixtures with mart reads, cached in memory.

## General rules (KICKOFF §7)

- Plan before coding; small, descriptive commits; never commit `data/` or `.env`.
- Tests use small fixtures trimmed from real files, especially for the parsers.
- Label as **simulated** everything that comes from the private-data adapters and from fixtures.
- Don't invent URLs, IDs or columns; confirm them at the source or mark them "not verified".
- Contract changed? Update `data-contract.md`, the Pydantic models and `openapi.json` together, and
  regenerate the client in the app.
- Out of scope for now: Airflow deployment, BigQuery load, models, Fleet API mock, commercial-module
  logic, real mart reads in the API.

## Sibling repos

All three live in `~/Documents/repos/basecast/`:

- `basecast-airflow` (front A): produces the data (local lake now, marts later).
- `basecast-get-data` (this repo, front C): serves the data and owns the contract.
- `basecast-app` (front B): its BFF calls this API through the client generated from `openapi.json`; the
  token never reaches the browser.

Reference only: `~/Documents/repos/fundsys/fundsys-analytics-get-data` is the Fundsys get-data. It plays
the same role, but it dispatches every request by a `process` field; basecast uses typed endpoints
instead.

## Docs

- `docs/KICKOFF.md`: the full kickoff, including tasks C0–C2 and the open questions (§8).
- `docs/data-contract.md`: the contract draft (source of truth once reviewed).
