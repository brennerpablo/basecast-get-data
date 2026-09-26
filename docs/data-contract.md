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

### 6. Pipeline, lake e tabelas (página /data)
Implementado (os primeiros routers do get-data). O schema exato de cada resposta está no `openapi.json`;
aqui ficam as regras.

**Lake (arquivos brutos).** O índice vem dos `_manifest.json` de cada pasta `dt=` (em memória, refeito a
cada 10 min). Só `raw/`, `parquet/` e `derived/` são navegáveis; o resto do bucket (`backups/`, `_logs/`)
é recusado.

- `GET /lake/sources`: por fonte, `source_id`, `name`, `group`, `publisher`, `upstream_url`,
  `catalog_id`, `schedule` (cron em palavras, America/Chicago), `files`, `bytes`, `snapshots`,
  `first_dt`, `last_dt`, `last_fetched_at`, `formats[]`, `datasets[]` (do `dataset_registry`, com
  `loaded` e `rows`) e `last_runs[]` (último `etl_run` por estágio). Substitui o rascunho `GET /sources`.
- `GET /lake/list?prefix=&q=&recursive=&offset=&limit=`: pastas (`source=`, `dt=`) e objetos sob um prefixo.
- `GET /lake/object?key=`: a entrada do manifest, o visualizador (`viewer`), os datasets da fonte, as
  linhas do `lake_processed` desse arquivo (`current: false` se o arquivo mudou depois) e o mesmo
  arquivo em outros snapshots.
- `GET /lake/object/structure?key=&member=`: abas de planilha, membros de zip, colunas e total de linhas,
  árvore de um JSON.
- `GET /lake/object/rows?key=&member=&sheet=&offset=&limit=&with_summary=`: um bloco de linhas. Planilha e
  texto delimitado são posicionais (colunas A, B, C…, sem adivinhar cabeçalho); Parquet e JSON mantêm
  nomes e tipos.
- `GET /lake/object/text?key=&member=`: texto por slide (pptx), por seção (docx) ou da página (html).
- `GET /lake/object/url?key=`: URL assinada V4 de 10 min, ou `null` quando o serviço não pode assinar.
- `GET /lake/object/content?key=&member=`: os bytes, com `Range` (o pdf.js lê PDFs por partes).

**Tabelas tratadas.** Postgres `basecast.public` (como `basecast_reader`) e BigQuery `basecast`.

- `GET /tables`: `dataset_registry` cruzado com o que existe; `kind` = `dataset` | `system` |
  `unregistered`, `declared`, `loaded`, `rows` (no Postgres é a estimativa do `pg_class`,
  `rows_estimated: true`), `bytes`.
- `GET /tables/{name}`: o mesmo, mais `columns[]`, `key_columns`, `partition_field`, `cluster_fields`.
- `GET /tables/{name}/rows?offset=&limit=&sort=&desc=&filter=&with_summary=`: um bloco. `limit` até 1.000 e
  janela de 100.000 linhas (além disso, ordenar ou filtrar). `filter=<coluna>:<op>:<valor>`, repetível,
  combinado com AND; ops `eq ne contains starts in gte lte gt lt between null notnull`; `in` e `between`
  separam valores com U+001F. `with_summary=true` traz `total`: exato no Postgres quando cabe em 6 s,
  senão a estimativa (`total_estimated: true`). No BigQuery, bloco sem filtro nem ordenação sai do
  `tabledata.list` (sem query); com filtro ou ordenação, query com `maximum_bytes_billed`.
- `GET /tables/{name}/lineage?offset=&limit=`: os arquivos brutos que alimentaram a tabela
  (`lake_processed`).

Blocos de linhas (`/lake/object/rows` e `/tables/{name}/rows`) têm a mesma forma: `columns[]` (`name`,
`type` = `text` | `number` | `date` | `boolean` | `json` | `geometry`, `source_type`), `rows` (listas na
ordem das colunas), `offset`, `limit`, `total`, `total_estimated`. Datas saem em ISO 8601; geometrias
resumidas (`POLYGON · 1023 points`).

**Execuções.** `GET /pipeline/runs?source=&stage=&status=&offset=&limit=`: `run_id`, `source`, `stage`
(`raw` | `process`), `dag_id`, `task_id`, `started_at`, `finished_at`, `duration_s`, `status`, `rows`,
`files`, `files_skipped`, `bytes`, `error`, mais `total`.

### 7. Log operacional (`ops.log`, tela /ops do app)
Não é endpoint: é a tabela onde app, get-data e airflow escrevem o mesmo formato de log, e que a tela
`/ops` do app lê direto do Postgres (a tela de monitoramento não pode depender do get-data estar no ar).
Postgres `basecast`, schema `ops`; o DDL é o model `OpsLog` em `basecast-app/prisma/schema.prisma`
(aplicado com `prisma db push`), as permissões em `basecast-app/prisma/ops-grants.sql`. Guardada 30 dias.

Uma linha por evento. Colunas:

- `ts` (timestamptz, UTC), `service` (`app` | `get-data` | `airflow`), `env` (`production` |
  `preview` | `development`), `level` (`info` | `warn` | `error`; `debug` só no stdout).
- `event`: nome fixo com ponto (lista abaixo); `message`: a frase legível.
- `request_id`: um clique do app até o get-data; o app manda no header `x-request-id` e o get-data
  grava o mesmo valor. `run_id`: o `etl_run.run_id`, em toda linha de uma execução de pipeline.
- `user_id` (id do usuário do app), `method`, `route` (o template da rota, nunca a URL crua nem a query
  string), `status` (HTTP), `duration_ms`.
- `error_class`, `error_stack` (até 15 linhas), `fingerprint` (`service:error_class:rota-ou-fonte`,
  sem ids nem números, para agrupar o mesmo erro).
- `version` (commit ou revisão), `host` (região do Vercel, instância do Cloud Run ou máquina),
  `context` (jsonb com o resto, sem segredos: chaves como `password`, `token`, `authorization` e
  `email` saem como `[redacted]`).

Eventos:

- Todos: `http.request`, uma linha por request (5xx `error`, 4xx `warn`, acima de 1,5 s `warn`,
  o resto `info`).
- app: `server.error` (erro não tratado fora de uma rota do BFF), `auth.sign_in`,
  `auth.sign_in_failed`, `http.upstream` (cada chamada ao get-data).
- get-data: `mart.load`, `instance.start`.
- airflow: `etl_run.start`, `etl_run.success`, `etl_run.partial`, `etl_run.failed`,
  `etl_run.abandoned`, e um `etl.<kind>` por evento do `EtlRun` (`etl.http`, `etl.error`,
  `etl.dataset`…); as demais linhas `warn`/`error` dos loggers `basecast_pipelines` e `basecast_dags`
  entram como `log`.

Regra comum: escrever o log nunca derruba o request nem a execução. Sem banco, a linha continua no
stdout.
