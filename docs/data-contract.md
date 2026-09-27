# Data contract — basecast (v2)

> Fonte da verdade entre `basecast-airflow` (produz os marts), `basecast-get-data` (serve) e `basecast-app`
> (consome). Mudou aqui? Atualize no mesmo movimento os modelos Pydantic (`basecast_get_data/schemas/`), as
> fixtures (`scripts/make_contract_fixtures.py`) e o `openapi.json`, e avise a sessão do app para regenerar o
> client (`npm run api:generate`). O schema exato de cada resposta está no `openapi.json`; aqui ficam as regras,
> os campos que importam e de que mart cada recurso sai.
>
> v2 (2026-09-26): §1–§5 reescritas para o build dos módulos (BUILD_C). §6 e §7 seguem como estavam.

## Convenções

- **Chaves**
  - `account_id`: o `ccn_no` da PUCT, como string. O `utility_id` do EIA vira o atributo `eia_utility_id`.
  - `county_fips`: string de 5 dígitos (ex.: `"48453"`).
  - `weather_zone`: `COAST`, `EAST`, `FWEST`, `NCENT`, `NORTH`, `SCENT`, `SOUTH`, `WEST`; `ERCOT` é o sistema.
  - `load_zone`: códigos da ERCOT (ex.: `LZ_NORTH`, `LZ_HOUSTON`, `LZ_AEN`).
  - `inr`: ID do projeto na fila de geração (ex.: `"20INR0290"`).
- **Tipos:** MW como float; anos como int; datas ISO 8601 (`YYYY-MM-DD`); instantes ISO 8601 em UTC.
- **Proveniência por linha:** toda linha que tem valor lido por máquina e não conferido (decks de grandes
  cargas) traz `verified: false`; todo valor de adapter privado traz `simulated: true`. O app põe o selo ao
  lado do número, não só no topo da página.

### Envelope

Toda resposta de recurso traz `meta` e `data` (a exceção é o catálogo `GET /caveats`).

```json
{
  "meta": {
    "generated_at": "2026-09-26T15:00:00Z",
    "data_as_of": "2026-09-26",
    "model_version": "a1b2c3d-accounts.1",
    "simulated": false,
    "verified": true,
    "sources": ["mart_accounts"],
    "caveats": [{"code": "weights_pending_review", "label": "Weights pending review", "text": "…"}]
  },
  "data": {}
}
```

- `data_as_of` e `model_version` saem das linhas do mart principal (`as_of`, `model_version`). `model_version`
  substitui o antigo `model_run_id`.
- `simulated`: verdadeiro quando algum valor vem de fixture ou de adapter privado simulado.
- `verified`: falso quando algum valor da resposta foi lido por máquina e não conferido. O detalhe fica em cada
  linha (`verified` por linha).
- `sources`: os marts que a resposta leu.
- `caveats`: as ressalvas que valem para a resposta, cada uma com o texto padrão (abaixo): as do recurso mais
  as que cada mart lido declara no `mart_meta` (chave `caveats`; um código fora da lista é descartado com log). O app mostra
  `label` como selo e `text` como tooltip, e nunca escreve ressalva por conta própria.
- Valores de uma tela que não são linhas (quebras da legenda, pesos, datas do backtest, variantes) vão em
  `data`, não em `meta`, para `Meta` ser um tipo só.

### Ressalvas (`CaveatCode`)

Lista fechada. O texto vive em `basecast_get_data/schemas/caveats.py`; `GET /caveats` devolve o catálogo
inteiro. Os textos não levam números que um rebuild possa mudar.

| Código | Rótulo | Quando |
|---|---|---|
| `machine_read_unverified` | Machine-read, not verified | alguma linha com `verified = false` (grandes cargas, forecast, backtest) |
| `preliminary_actuals` | Preliminary actuals | o real de 2026 ainda não liquidado (backtest) |
| `weights_pending_review` | Weights pending review | contas, enquanto `weights_status = pending_review` |
| `band_uncalibrated` | Band not calibrated | forecast com faixa `p10_p90`; backtest |
| `beyond_backtested_window` | Beyond the backtested window | fila ajustada no horizonte dez/2028 (passa dos 24 meses testados) |
| `allocated_statewide` | Allocated from a statewide forecast | forecast de uma zona |
| `by_county_not_point` | By county, not by point | contas (gatilhos), data centers |
| `by_area_not_homes` | By land area, not homes | mapa de aquisição |
| `requests_not_forecasts` | Requests, not forecasts | card do G&T (P1) |
| `policy_pause_2026` | Approvals paused on 2026-08-03 | grandes cargas |
| `optimistic_weather` | Observed weather (optimistic) | curva de despacho do 4CP (P1) |
| `simulated` | Simulated | dado de adapter privado (P2) |
| `fixture` | Fixture | toda resposta servida das fixtures |

### Erros

- **503 `{"detail": "mart_not_built", "mart": "<nome>"}`** quando o mart do recurso não existe. O corpo é
  exatamente esse (schema `MartNotBuilt`), e o app o mostra como estado vazio. A API nunca cai para fixture
  em silêncio.
- **404 `{"detail": "not_found"}`** para qualquer id desconhecido. Uma conta retida pela trava de validação
  responde igual, sem dica de que existe.
- **503 `{"detail": "database unavailable"}`** quando o Postgres não responde (modo marts).
- **422** para parâmetro fora do domínio (o padrão do FastAPI); no `GET /backtest/peak`, um `as_of` inválido
  responde `{"detail": "invalid_as_of", "as_of_dates": [...]}`.

### Fixtures e marts

Cada recurso de §1–§5 lê frames no formato dos marts, de uma de duas origens:

- **marts:** `public.mart_*` no Postgres (como `basecast_reader`), carregados inteiros na memória no primeiro
  uso ou na subida, e conferidos a cada `MART_TTL_S` (10 min): o mart só é recarregado quando mudam o número
  de linhas, `built_at`, `model_version` ou `as_of`. Cada carga escreve um evento `mart.load` (por enquanto
  só no stdout, no formato do §7; o INSERT no `ops.log` espera o papel próprio da API). Um mart que não existe,
  ou que não tem as colunas que a API lê, responde 503 `mart_not_built`; nunca cai para fixture.
- **fixtures:** `basecast_get_data/data/fixtures/<mart>.json`, cópias pequenas e inventadas dos marts, com os
  mesmos nomes e colunas, geradas por `scripts/make_contract_fixtures.py` (seed fixa).

A origem é escolhida por grupo de recursos (`accounts`, `explorer`, `forecast`, `backtest`):
`DATA_MODE=marts` põe todos nos marts; com `DATA_MODE=fixtures`, só os grupos de `MARTS_LIVE` (variável, ou
o padrão em `config.py`) leem os marts. Produção troca grupo por grupo, à medida que a sessão A publica
marts que passaram nos checks. `GET /health` diz `data_mode`, `marts_live` e os marts em memória.

Sobre as fixtures: Só as chaves de condado são reais. As contas são fictícias
(`FX001`…, nomes inventados), então nenhuma conta retida pode aparecer numa fixture. Toda resposta de fixture
traz `simulated: true` e o caveat `fixture`. As fixtures também são a referência de formato de cada linha
para a sessão A.

Valores de build que não são linhas ficam no mart `mart_meta` (`mart`, `key`, `value` jsonb): `accounts.signals`,
`county_acquisition.legend_breaks` / `grid_tilt` / `signals`, `peak_forecast.default_variant` / `variants` /
`ratio_definition`, `peak_backtest.eras`, `glossary.items`.

### Glossário (`GET /glossary`)

Sem envelope, como `/caveats`: `items[]` com `kind` (`trigger` | `flag` | `next_action`), `code`, `label`
(curto, para chip e coluna), `text` (o significado, para tooltip) e `strength` (`strong` | `context`, só em
gatilho). A fonte é o `mart_meta` (`glossary.items`), escrito pela sessão A a partir do config de gatilhos
(X5), então uma mudança de força (R12) chega sozinha. O app mostra esses rótulos e não escreve os seus.

### Trava de validação

Enquanto a A-M8 não rodar: nenhum schema tem `is_base_partner` (um teste lê o `openapi.json`); `/accounts`
devolve exatamente as linhas do `mart_accounts` (107 contas); um `account_id` fora do mart responde o 404
comum.

## Recursos

### 1. Explorer: condados e fila de geração

`GET /geo/counties?horizon=2027|2028&stratum=all|solar|storage|wind|gas_other` (padrão 2028, `all`).
Os 254 condados num payload só; o mapa troca de camada no cliente.

- `data.items[]`: `county_fips`, `county_name`, `weather_zone`, `in_ercot` e três blocos:
  - `acquisition` (X14; `null` fora da ERCOT): `priority`, `rank`, `priority_class` (1–5), `market_score`,
    `grid_score`, `grid_factor`, `channel` (`retail_direct` | `partnership` | `mixed`), `partner_type`,
    as participações por área (`retail_share`, `coop_share`, `muni_share`, `outside_share`, `partner_share`,
    `addressable_share`), `retail_rank` e `partner_rank` (`null` = fora da lista do canal), `n_partners`,
    `top_partner_share`, `drivers[]`, `drags[]`;
  - `queue` (X2; `null` sem projeto ativo no estrato): `projects`, `projects_ia`, `raw_mw`, `raw_mw_ia`,
    `adj_mw` (MW esperados em COD até dezembro do horizonte), `ratio`, `rank_raw`, `rank_adj`, `rank_change`
    (= `rank_raw − rank_adj`, calculados pela API dentro do estrato e do horizonte), `large_gas_mw_2028`;
  - `data_centers` (Q4): `sites` (só os da ERCOT), `sites_naics_only` (para o toggle "include NAICS-only
    matches") e `sites_outside_ercot` (os que o Q4 põe fora da ERCOT: marcados e fora das contagens, R7). Cada
    site do detalhe traz `in_ercot`.
- `data`: `horizon`, `stratum`, `horizons`, `strata`, `queue_as_of_month`, `legend` (`breaks[4]`, `classes`),
  `weights` (`signals[]` com bloco e peso, `grid_tilt`).
- Marts: `mart_county_acquisition`, `mart_queue_adjusted_county` (último `as_of_month`),
  `mart_data_center_sites_new`, mais a tabela `county_weather_zone` para os 254 condados. A fila entra por join
  em `county_fips`, nunca copiada para o mart de aquisição.
- Caveats: `by_area_not_homes`, `by_county_not_point`; `beyond_backtested_window` com `horizon=2028`.

`GET /geo/counties/{county_fips}`: o painel do condado. `acquisition`; `signals[]` (os 8 sinais com `raw`, `pct`,
bloco e peso); `queue[]` por estrato com os dois horizontes; `top_projects[]` (10 maiores por MW esperados em
dez/2028); `data_centers[]`; `accounts[]` (co-ops e munis que cobrem ≥ 1% do condado, com `county_share`,
`rank`, `tier`, `next_action`; de `mart_account_counties`, só contas do universo pontuado). 404 para FIPS
desconhecido.

`GET /queue/projects?county=&stratum=&stage=entry|ia&zone=&q=&sort=&desc=&offset=&limit=` (até 500): os projetos
ativos do último relatório GIS (`mart_queue_project_scores`): `inr`, `project_name`, condado e zonas,
`fuel_type`, `stratum`, `stage`, `stage_date`, `elapsed_months`, `capacity_mw`, `projected_cod` (a data do
desenvolvedor), `curve`, `p_cod_2027`, `p_cod_2028`, `mw_2027`, `mw_2028`, `clamped_2027`, `clamped_2028`.
Padrão: `sort=mw_2028`, decrescente.

Fora do contrato: `organic_peak_growth_mw` e `permits_units` por condado, P10/P90 no mapa (R10), fila além de
dez/2028 (P2).

`GET /geo/zones?measure=` (P1; `mart_zone_layers`, `mart_county_large_load`): camadas por weather zone, agrupadas
por medida (`layers[]`: `measure`, `label`, `unit`, `method`, `zones[]` com `central`, `low`, `high`,
`verified`). As medidas são `excess_share`, `min_max_ratio_2019` e `min_max_ratio_2026` (X1: onde a carga plana
chegou), `a2e_stock`, `pipeline_2032` e `u_share` (X11; alocadas com faixa, lidas por máquina). `counties[]`:
o estoque aprovado alocado por condado (`allocated_a2e_mw`) e os condados que a ERCOT nomeia
(`named_by_ercot`, com os MW observados), que o mapa desenha como pontos. O pipeline nunca é espalhado pelos
alvarás. Caveat `allocated_statewide`.

### 2. Grandes cargas por zona

Não há recurso de grandes cargas por condado nem por zona com previsão própria: as grandes cargas são
estaduais e alocadas (X7, X11). O que existe por zona está no forecast (§3, caveat `allocated_statewide`) e,
no P1, em `GET /geo/zones`.

### 3. Forecast

`GET /forecasts/peak?region=ERCOT|<zona>&variant=deck_pre_batch_zero|deck_latest|approvals_pace`

- `variant` omitido = o padrão do build (`mart_meta.peak_forecast.default_variant`, R13).
- `data.series[]` (o total) e `data.layers[]` (`organic`, `large_load`, `unattributed`): `target_year`,
  `p10_mw`, `p50_mw`, `p90_mw`, `band_kind`, `verified`.
  - `band_kind`: `p10_p90` (faixa probabilística), `allocation_range` (o mínimo e o máximo entre as divisões
    candidatas das zonas; **não** é P10–P90) ou `null` (sem faixa, como em `approvals_pace`).
  - `band_basis`: de que a faixa é feita (`rolling_rmse`, `ratio_draws`, `u_draws`, `independent_layers`,
    `in_sample_weather`, `allocation_variants`); o texto de cada código vem em `data.band_basis`, para o tooltip.
- `data.official[]`: as previsões oficiais da mesma região (`product`, `vintage`, `vintage_date`, `series` =
  `ercot_adjusted` | `tsp_provided` | `cdr`, `label`, `target_year`, `mw`), de `mart_official_peak_lines`.
- `data.inputs`: `deck_vintage`, `factor`, `ratio_p10/p50/p90`, `approved_stock_mw`, `share_of_ll_u` (zonas),
  `verified`.
- `data.variants[]`: `variant`, `label`, `is_default`, `has_band`, `regions[]` (onde a variante existe; no P0
  as zonas só têm a variante padrão). `data.available = false` e séries vazias quando a combinação não existe.
- Marts: `mart_peak_forecast` (o `as_of` mais recente é o run atual), `mart_official_peak_lines`.
- Caveats: `machine_read_unverified` quando alguma linha tem `verified = false`; `band_uncalibrated` quando há
  faixa `p10_p90`; `allocated_statewide` numa zona.

`GET /forecasts/large-load`

- `realization[]` (Q5/X7, `mart_large_load_realization`): safra do deck × ano-alvo com o prometido, o
  aprovado e as razões; `document` e `page` para o link do slide; `realized_partial` (o ano-alvo ainda não
  acabou: o estoque é o do `realized_month`).
- `ratio_band`: a faixa da razão que a variante padrão da ERCOT usa (sai do `mart_peak_forecast`, para o gráfico
  e o forecast nunca divergirem), com `definition`.
- `in_service[]` (`mart_large_load_in_service`): safra × ano × status (`approved_to_energize`,
  `planning_studies_approved`, `under_ercot_review`, `no_studies_submitted`), MW acumulados.
- `monthly[]` (`mart_large_load_monthly`): estoque aprovado mês a mês e o pico observado; `null` = mês sem
  leitura.
- `annotations[]` (`mart_annotations`, P1): eventos datados com `source_url` (`null` = fonte não verificada);
  vazio enquanto o mart não existe, sem derrubar o resto da aba.
- Todas as linhas trazem `verified`. Caveats: `machine_read_unverified`, `policy_pause_2026`.

Fora do contrato: `generation_added_mw`, `region_type=county` e `region_type=utility` (volta só no P2, simulado).
P1 (implementados, grupo `forecast`; cada endpoint só dá 503 se faltar o seu próprio mart):

- `GET /forecasts/queue-curves?stratum=&stage=&weighting=mw|count` (`mart_queue_stage_curves`): `curves[]` por
  etapa (`entry`, `ia`), estrato e peso, com `points[]` (`month`, `at_risk`, `cif_cod`, `cif_withdrawn`,
  `survival`, `supported`); onde há menos de 10 em risco (`supported = false`) a API anula os valores.
  `milestones[]`: COD em 12, 24, 36 e 48 meses.
- `GET /load/normalized?region=` (`mart_load_normalized_monthly`, `mart_load_normalized_annual`): `monthly[]`
  (média, energia e pico, real e a clima normal, temperatura, variação em 12 meses; `complete = false` no mês
  corrente) e `annual[]` (energia e o pico de verão com P10/P50/P90 sob os anos de clima normal), mais
  `normal_period` e `weather_source` do `mart_meta`.
- `GET /four-cp` (`mart_four_cp_intervals`, `_zone`, `_dispatch_curve`, `_scarcity`, `_rates`): intervalos por
  ano, carga das zonas nos CPs, curva dias de despacho × acerto, deslocamento da escassez, tarifas
  (`docket`, `status` final | pending, `billed_year`) e a janela da oferta (`mart_meta`). Caveats
  `optimistic_weather` e, enquanto o último verão não fechar, `preliminary_actuals`.

### 4. Backtest

`GET /backtest/peak?as_of=` (padrão: a data mais recente)

- `as_of_dates[]` (as 8 datas) e `cells[]` da data escolhida (`mart_peak_backtest`): `as_of`, `target_year`,
  `horizon`, `source` (`basecast`, `basecast_organic_only`, `LTLF`, `CDR`…), `product`, `vintage`, `variant`,
  `era`, `p10/p50/p90_mw` (oficiais só `p50_mw`), `actual_mw`, `actual_final`, `error_pct`, `in_band`, as
  camadas (`organic_p50`, `ll_p50`, `u_p50`, `ll_realized`, `u_realized`), `leak_note`, `verified`.
- `scores[]`: por `era` (e `era = all`) e fonte, sobre todas as datas: `n`, `mape` (média de |`error_pct`|),
  `bias_pct` (média de `error_pct`), `coverage` (parcela em `in_band`).
- `comparisons[]`: para cada fonte oficial, o basecast e a fonte nas mesmas células, pareadas por
  (`as_of`, `target_year`), por era e no total. É a tabela "3,3% × 5,1%" do X7.
- `ablation[]`: `basecast` × `basecast_organic_only` em `era = all`.
- `eras[]` (de `mart_meta`), `actuals[]` (`mart_actual_summer_peaks`), `fan_target_year` e `fan[]`
  (`mart_backtest_fan`: as previsões oficiais do último verão, a faixa da própria ERCOT, o real e o nosso modelo
  em cada data; `kind` = `official_preliminary` | `official_range` | `official` | `actual` | `model`).
- Caveats: `band_uncalibrated`, `machine_read_unverified`, `preliminary_actuals` enquanto o último verão não
  estiver liquidado.

`GET /backtest/official-errors?product=`: a matriz safra × ano-alvo (`mart_official_forecast_errors`) e o resumo
por produto e horizonte (`n`, `mape`, `bias_pct`), mais a lista de produtos.

`GET /backtest/queue`: o modelo da fila refeito em relatórios passados, 24 meses à frente
(`mart_queue_backtest`): `raw_mw`, `pred_mw`, `actual_mw`, `developer_projected_mw`, `error_pct` por data e
estrato, e `county_rank[]` com o Spearman por condado (ajustada, bruta, datas dos desenvolvedores).

### 5. Contas (inteligência comercial)

`GET /accounts?type=&tier=&next_action=&trigger=&zone=&gt=&county=&q=&sort=&desc=&rank_scope=all|within_type`

- Filtros de categoria aceitam vários valores (`tier=A&tier=B`). `trigger`: contas com algum desses gatilhos
  ativos. `county`: contas que cobrem ≥ 1% do condado (`mart_account_counties`). `q`: nome contém.
- `sort`: `rank` (padrão), `score`, `name`, `meters`, `latest_event_date`, `action_changes_on`; nulos por
  último. Com `rank_scope=within_type`, `sort=rank` ordena por tipo e `rank_within_type`.
- `data.items[]` (`mart_accounts`): `account_id`, `name`, `account_type` (`coop` | `muni`), `eia_utility_id`,
  `gt` (G&T; substitui `parent_utility_id`), `primary_weather_zone`, `meters`, `score`, `rank`,
  `rank_within_type`, `tier` (`A` | `B` | `C`), `signals` (`{signal: {raw, pct}}`), `next_action`
  (`call_now` | `nurture` | `watch` | `hold`), `action_changes_on` (quando a ação expira sem evento novo),
  `n_strong`, `n_context`, `latest_event_date`, `top_trigger` (`trigger`, `title`, `event_date`, `age_days`),
  `active_triggers[]`, `flags[]`, `simulated`.
- `data`: `total`, `rank_scope`, `signals[]` (rótulo, unidade e peso de cada sinal), `weights_set`,
  `weights_status`.
- Caveats: `weights_pending_review` (enquanto `weights_status = pending_review`), `by_county_not_point`.

`GET /accounts/export.csv` (mesmos filtros): `text/csv` em streaming, anexo `basecast-accounts-<as_of>.csv`,
as mesmas linhas da lista com `top_trigger` e `signals` achatados (`raw_<sinal>`, `pct_<sinal>`) e listas
separadas por `; `.

`GET /accounts/{account_id}`: o diagnóstico do X9 §4, servido do `payload` de `mart_account_detail`.

- `header[]` e `territory.facts[]` são Facts: `{key, label, value, unit, source, as_of, note, simulated,
  verified}`; `value = null` é lacuna, nunca zero.
- `score` (sinais com peso configurado, peso usado e contribuição; as contribuições somam o score),
  `next_action` (`rule`, `lead_trigger`, `offer`, `talking_points`, `changes_on`, `changes_to`),
  `triggers` (`active[]` com os eventos fortes ativos, `context_summary[]` com uma linha por gatilho de
  contexto, `history_count`), `territory` (`counties[]`, `context_rule` = `exposed` | `home_county`,
  `context_label`, `zones[]`, `data_centers[]`, `queue[]`, `zone_outlook`), `eia_series[]`, `gaps[]`,
  `coverage` (`public_data`, `utility_private_data`, `fleet_data`, `resolution` = `zone` |
  `territory (simulated)`).
- Blocos P1 no mesmo payload (vazios até o mart trazê-los):
  - `suppliers[]` (X13): `gt`, `tsp`, `via`, `fact` (a frase), `path[]` (MW pedidos em 2026, 2030, 2032),
    `share_of_rfi`, `n_accounts`, `filed_date`, `source_ref`, `fires_trigger`, `verified` (falso: lido do RFI).
    Com fornecedor, a resposta leva o caveat `requests_not_forecasts`.
  - `four_cp_offer` (X3 + X15): `zone`, `zone_line`, a janela, `dispatch_days`, `rates[]` (US$/MW-ano por ano de
    cobrança, `docket`, `status` final | pending, `billed_year`), `note` (custo evitado pela co-op, não receita
    da Base) e `account_4cp` (um Fact vazio: a carga da conta no 4CP é dado privado). Leva `optimistic_weather`.
  - `city` (X10, só munis): `place_name`, `place_fips`, `fit` (`same` | `city_larger` | `territory_larger`),
    as participações por área e `facts[]` rotulados como cidade, não território.

`GET /accounts/{account_id}/events?since=&trigger=&strength=&offset=&limit=`: o histórico completo, do mais novo
para o mais antigo (`mart_account_events`). Todo evento tem data e título (um projeto da TPIT sem nome usa o `detail`). Paginação por `offset`/`limit` (até 500), como em
`/pipeline/runs`.

Fora do contrato: `first_deficit_year`, `deficit_mw_p50_next_3y` (precisam da carga da conta: só no P2,
simulado), `forecast`/`deficit[]` por conta, `load_zones[]` e `is_base_partner` (até a A-M8).

P1: `GET /insights` (cards com valor, legenda, ressalva obrigatória e link; valores só dos marts).

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
  `unregistered`, `declared`, `loaded`, `inputs` (as tabelas que um mart lê, para a linhagem de /data/flow;
  `null` nos datasets dos parsers e enquanto o registry não tiver a coluna), `rows` (no Postgres é a estimativa do `pg_class`,
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
(`raw` | `process` | `model`, este último para os builds dos marts), `dag_id`, `task_id`, `started_at`, `finished_at`, `duration_s`, `status`, `rows`,
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
  `auth.sign_in_failed`, `http.upstream` (chamada ao get-data que falhou ou passou de 1,5 s; as
  outras ficam só no stdout, porque o `http.request` do app e o do get-data já cobrem).
- get-data: `mart.load`, `instance.start`.
- airflow: `etl_run.start`, `etl_run.success`, `etl_run.partial`, `etl_run.failed`,
  `etl_run.abandoned`, e um `etl.<kind>` por evento do `EtlRun` (`etl.http`, `etl.error`,
  `etl.dataset`…); as demais linhas `warn`/`error` dos loggers `basecast_pipelines` e `basecast_dags`
  entram como `log`.

Regra comum: escrever o log nunca derruba o request nem a execução. Sem banco, a linha continua no
stdout.

### 8. Insights (P1)

`GET /insights`: os números do vídeo em cards, na ordem da página (`mart_insights`, grupo `insights`).

- Só as linhas de `basecast-airflow/docs/analysis/video-candidates.md` com nota **A** ou **B**.
- `data.cards[]`: `id` (a linha: `A1`, `B3`…), `grade`, `rank`, `title`, `caption` (a frase, montada pelo build
  a partir dos valores dos marts), `value` e `unit` (o número principal), `figures[]` (os outros números da
  linha: `label`, `value`, `unit`), `caveat` (a ressalva obrigatória da linha, sempre mostrada com o card),
  `caveats[]` (códigos do catálogo, com texto), `queue` (`generation` | `large_load`: todo card de fila diz de
  qual fila é, porque as duas passam de ~438 GW), `verified` (todos os números re-derivados pelo X6),
  `depends_on[]` (itens de revisão pendentes), `source_doc` e `link` (a tela do app com a evidência).
- Nenhum número do card é escrito no app nem no get-data: `value`, `figures`, `caption` e `caveat` saem do
  build da sessão A, que confere cada valor contra o doc de origem (checks de ouro).
- `meta.verified` é falso quando algum card carrega o caveat `machine_read_unverified`.
