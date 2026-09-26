# Data contract — basecast (RASCUNHO)

> Rascunho inicial para a tarefa C0 do KICKOFF. Revise, ajuste e só então escreva os modelos Pydantic.
> Este arquivo é a fonte da verdade entre `basecast-airflow` (produz os dados), `basecast-get-data`
> (serve) e `basecast-app` (consome). Mudou aqui? Atualize os modelos, o `openapi.json` e regenere o
> client do app no mesmo movimento.

## Convenções

- **Chaves**
  - `county_fips`: string de 5 dígitos (ex.: `"48453"`).
  - `weather_zone`: `COAST`, `EAST`, `FWEST`, `NORTH`, `NCENT`, `SOUTH`, `SCENT`, `WEST`.
  - `load_zone`: códigos da ERCOT (ex.: `LZ_NORTH`, `LZ_HOUSTON`, `LZ_AEN`).
  - `utility_id`: ID do EIA, como string.
  - `inr`: ID do projeto na fila de geração (ex.: `"20INR0290"`).
- **Tipos:** MW como float; anos como int; datas e horários em ISO 8601 UTC.
- **Envelope:** toda resposta traz `meta` e `data`.

```json
{
  "meta": {
    "generated_at": "2026-09-26T15:00:00Z",
    "data_as_of": "2026-09-01",
    "model_run_id": "string | null",
    "simulated": false,
    "sources": ["ercot_gis", "ercot_native_load"]
  },
  "data": {}
}
```

`simulated: true` sempre que a resposta usar fixtures ou os adapters de dados privados simulados.

## Recursos

### 1. Métricas por condado (Explorer)
`GET /geo/counties/metrics?metric=<metric>&year=<int>`

- `metric`: `gen_queue_raw_mw`, `gen_queue_adjusted_mw`, `organic_peak_growth_mw`,
  `permits_units`, `acquisition_score`.
- Itens de `data.items`: `county_fips`, `county_name`, `weather_zone`, `value`, `p10`, `p90`, `unit`.

### 2. Métricas por zona (grandes cargas só existem agregadas)
`GET /geo/zones/metrics?zone_type=load_zone|weather_zone&metric=<metric>&year=<int>`

- `metric`: `large_load_raw_mw`, `large_load_adjusted_mw`, `peak_forecast_mw`.
- Itens: `zone_type`, `zone_id`, `value`, `p10`, `p90`, `unit`.

### 3. Previsões
`GET /forecasts?region_type=weather_zone|county|utility&region_id=<id>&metric=peak_mw`

- `data.series[]`: `year`, `p10`, `p50`, `p90`.
- `data.components[]` (decomposição do P50): `year`, `organic_mw`, `large_load_mw`,
  `generation_added_mw`.

### 4. Backtest
`GET /backtest?target=summer_peak&year=<int>`

- `data.series[]`: `label` (`official_preliminary`, `official_adjusted`, `actual`, `model`),
  `value_mw`, `low_mw`, `high_mw` (faixas quando a fonte dá intervalo), `source`, `method`
  (`file`, `manual`, `model`).

### 5. Contas (inteligência comercial)
`GET /accounts?in_ercot=true&type=coop|muni|gt`

- Itens: `utility_id`, `name`, `type`, `parent_utility_id` (cooperativa de geração e transmissão),
  `in_ercot`, `is_base_partner`, `priority_score`, `first_deficit_year`, `deficit_mw_p50_next_3y`,
  `top_trigger`.

`GET /accounts/{utility_id}`

- `profile`: `name`, `type`, `counties[]` (com a fração de área), `load_zones[]`.
- `forecast`: mesmo formato do recurso 3.
- `deficit[]`: `year`, `p10_mw`, `p50_mw`, `p90_mw`.
- `triggers[]`: `date`, `kind`, `description`, `evidence[]` (dataset + referência).
- `next_action`: `code`, `title`, `rationale`, `evidence[]`.
- `coverage`: `public_data: true`, `utility_private_data: bool`, `fleet_data: bool`, `resolution`
  (`zone` ou `territory`).

`GET /accounts/{utility_id}/export.csv`: a recomendação em CSV, para colar num CRM.

### 6. Pipeline e fontes (página /data)
`GET /pipeline/runs?source=<id>&limit=<int>`

- Itens: `source`, `started_at`, `finished_at`, `status`, `rows`, `files`, `error`.

`GET /sources`

- Itens: `source_id`, `catalog_id`, `last_success_at`, `data_start`, `data_end`, `verified`.
