# Field Ops — Hive Health Board

## 1. Stakeholder & decisions (simulated interview)
- Users: 2–3 beekeepers, Tarn-et-Garonne, leave for the field ~07:00 local
- Daily decisions:
  1. Which apiaries do I visit today, in what order?
  2. Which sensors need repair/replacement?
  3. Which colonies are at risk (swarm, queenless/brood loss, starvation, dead)?
- Current pain (assumed): alerts are noisy — rain days trigger false "weight loss" alarms;
  dead sensors are noticed only on site.

## 2. KPIs

| KPI | Definition | Window | Grain | Threshold (v0, to validate) | Source |
|---|---|---|---|---|---|
| data_completeness | received / expected readings | rolling 24h | hive | < 80% → WATCH, < 20% → SENSOR_DOWN | telemetry |
| last_seen_utc | latest reading timestamp | — | hive | > 6h → STALE | telemetry |
| weight_delta_24h | weight(t) − weight(t−24h), kg | 24h | hive | < −1.0 kg on a dry day → WATCH | telemetry + weather |
| weight_delta_7d | trend over 7 days, kg | 7d | hive | steady decline in active season → WATCH (starvation) | telemetry |
| max_drop_1h | largest weight drop within 1h, kg | 24h | hive | > 1.5 kg → ALERT (possible swarm) | telemetry |
| brood_temp_mean / std | internal temp, °C | 24h | hive | mean outside 33–37 or std > 1.5 → ALERT (brood loss) | telemetry |
| temp_drift | hive temp − apiary median | 7d | hive | persistent offset > 3 °C → SENSOR_DRIFT | telemetry (peers) |
| rain_mm, temp_ext | daily weather at apiary | day | apiary | used to suppress false weight alerts | Open-Meteo |
| visit_priority | weighted count of ALERT/WATCH/STALE hives | day | apiary | ranked list | derived |

Thresholds are v0 assumptions, to validate with a working beekeeper.

## 3. Cadence & freshness
- Daily snapshot ready by **06:30 Europe/Paris** (before field departure) — SLA
- Intraday refresh hourly (swarm alerts during active season)
- Active season Mar–Oct; winter uses relaxed weight thresholds
- Storage in UTC; `date_local` derived in Europe/Paris

## 4. Output contracts (marts)

### fct_hive_status_daily  — grain: hive × date_local
hive_id, apiary_id, date_local,
data_completeness_24h, last_seen_utc,
weight_kg_end, weight_delta_24h, weight_delta_7d, max_drop_1h,
brood_temp_mean, brood_temp_std, temp_drift_7d,
rain_mm, temp_ext_mean,
status (OK | WATCH | ALERT | SENSOR_DOWN | STALE),
status_reasons (list of rule codes)

### fct_apiary_visit_priority_daily  — grain: apiary × date_local
apiary_id, date_local, lat, lon, commune,
n_hives, n_alert, n_watch, n_sensor_issue,
priority_score, priority_rank

### Dimensions
dim_hive (hive_id, apiary_id, sensor_id, installed_at)
dim_apiary (apiary_id, geometry, rpg_parcel_id, crop, commune)
