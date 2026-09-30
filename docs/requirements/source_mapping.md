# Source mapping — Field Ops Hive Health Board

# 소스 매핑의 핵심은 두 가지. 마트의 모든 컬럼을 raw 필드까지 역추적하는 것, 그리고 소스 사이의 의존성(무엇을 먼저 수집해야 하는지)을 드러내는 것.

# 이 프로젝트에서 두 번째가 특히 중요. 합성 데이터가 실제 데이터에 기대서 생성되기 때문. 양봉장은 실제 RPG 필지 위에 놓이고, 텔레메트리는 실제 날씨에 반응해야 하니까. 그래서 수집 순서가 곧 설계.

Scope: Tarn-et-Garonne (dept 82), active season 2025-03-01 → 2025-10-31.
All timestamps stored UTC. Distances/areas computed in EPSG:2154, stored in EPSG:4326.

## 1. Source inventory

| # | Source | Type | Real / Synthetic | Access | Grain | Refresh |
|---|---|---|---|---|---|---|
| S1 | Dept 82 boundary + communes | vector | real | geo.api.gouv.fr (GeoJSON) | commune | static |
| S2 | RPG (Registre Parcellaire Graphique) | vector | real | IGN download (GeoPackage) | parcel | yearly |
| S3 | Apiary registry | table + point | synthetic, placed on S2 orchard parcels | generator | apiary | static (v0) |
| S4 | Hive & sensor registry | table | synthetic | generator | hive, sensor assignment (valid_from/valid_to) | static (v0) |
| S5 | Weather (hourly + daily) | time series | real | Open-Meteo archive API | apiary × hour | daily |
| S6 | Hive telemetry | time series | synthetic, driven by S5 | generator | sensor × 15 min | hourly |
| S7 | Injected fault log | table | synthetic ground truth | generator | event | — |

## 2. Dependency order (ingest DAG)

S1 → S2 (clip to dept) → S3 (place apiaries on orchard parcels) → S4
S3 → S5 (one weather series per apiary coordinate)
S4 + S5 → S6 (telemetry reacts to weather) → S7 written alongside S6

## 3. Raw schemas (what lands in data/raw/, as fetched)

S5 weather_hourly: apiary_id, ts_utc, temperature_2m, precipitation, wind_speed_10m
S6 telemetry:      sensor_id, ts_utc, weight_kg, temp_internal_c, humidity_pct, battery_v
S4 sensor_assignment: sensor_id, hive_id, valid_from_utc, valid_to_utc (null = active)
S7 fault_log:      event_id, sensor_id|hive_id, fault_type, start_utc, end_utc
    fault_type ∈ {DROPOUT, DRIFT, SWARM, BROOD_LOSS, STARVATION, SENSOR_SWAP}

## 4. Column lineage (mart ← raw)

| Mart column | Derivation | Raw fields | Source |
|---|---|---|---|
| hive_id | sensor_id joined to assignment valid at ts_utc | sensor_id, ts_utc | S6, S4 |
| data_completeness_24h | count(readings) / 96 | ts_utc | S6 |
| last_seen_utc | max(ts_utc) | ts_utc | S6 |
| weight_kg_end | last weight of date_local | weight_kg | S6 |
| weight_delta_24h / 7d | lag diff on daily end weight | weight_kg | S6 |
| max_drop_1h | min of rolling 1h diff | weight_kg, ts_utc | S6 |
| brood_temp_mean / std | 24h agg | temp_internal_c | S6 |
| temp_drift_7d | hive mean − apiary median, 7d | temp_internal_c | S6, S4 |
| rain_mm, temp_ext_mean | daily agg of hourly | precipitation, temperature_2m | S5 |
| apiary geometry, commune, crop | point-in-parcel, point-in-commune | geometry, crop code | S3, S2, S1 |
| status, status_reasons | rule engine, priority SENSOR_DOWN > STALE > ALERT > WATCH > OK | all above | derived |

## 5. Known data risks
- Sensor swap: without S4 valid_from/valid_to, history of a hive splits in two. (SCD2-style join)
- Timezone: date_local boundaries shift with DST (Mar/Oct inside the season).
- RPG crop codes change between years → pin to one RPG vintage.
- Synthetic ≠ reality: S7 ground truth used only to validate rules, never as a mart input.
