"""Read-only queries behind the Field Ops board. Every function opens a short-lived
connection: a board left open must never hold the lock that `dbt build` needs."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

DEFAULT_WAREHOUSE = Path("data/lake/warehouse.duckdb")

# What the beekeeper must do at an apiary, most urgent first
NEEDS = ["ALERT", "SENSOR", "WATCH", "OK"]


def warehouse_path() -> Path:
    return Path(os.environ.get("HIVE_WAREHOUSE", DEFAULT_WAREHOUSE))


@contextmanager
def connect(path: Path | None = None):
    con = duckdb.connect(str(path or warehouse_path()), read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        yield con
    finally:
        con.close()


def _df(sql: str, params: list | None = None, path: Path | None = None) -> pd.DataFrame:
    with connect(path) as con:
        return con.execute(sql, params or []).df()


def season_bounds(path: Path | None = None) -> tuple[date, date]:
    row = _df("select min(date_local) as lo, max(date_local) as hi "
              "from marts.fct_hive_status_daily", path=path).iloc[0]
    return row["lo"].date(), row["hi"].date()


def beekeepers(path: Path | None = None) -> list[str]:
    return _df("select distinct beekeeper_id from marts.dim_apiary order by 1",
               path=path)["beekeeper_id"].tolist()


def status_counts(day: date, beekeeper: str | None = None,
                  path: Path | None = None) -> pd.DataFrame:
    """Hives per status on one day."""
    return _df("""
        select s.status, count(*) as hives
        from marts.fct_hive_status_daily as s
        join marts.dim_apiary as a using (apiary_id)
        where s.date_local = $1 and ($2 is null or a.beekeeper_id = $2)
        group by s.status
    """, [day, beekeeper], path)


def apiary_map(day: date, beekeeper: str | None = None,
               path: Path | None = None) -> pd.DataFrame:
    """One point per apiary, with what it needs today (ALERT > SENSOR > WATCH > OK)."""
    return _df("""
        select
            apiary_id, beekeeper_id, commune_name, crop_variety, lat, lon,
            n_hives, n_alert, n_sensor_issue, n_watch, priority_score, priority_rank,
            array_to_string(reasons, ', ') as reasons,
            case when n_alert > 0 then 'ALERT'
                 when n_sensor_issue > 0 then 'SENSOR'
                 when n_watch > 0 then 'WATCH'
                 else 'OK' end as need
        from marts.fct_apiary_visit_priority_daily
        where date_local = $1 and ($2 is null or beekeeper_id = $2)
        order by apiary_id
    """, [day, beekeeper], path)


def visit_list(day: date, beekeeper: str | None = None,
               path: Path | None = None) -> pd.DataFrame:
    """Apiaries to visit, in order, per beekeeper."""
    return _df("""
        select beekeeper_id, priority_rank as rank, apiary_id, commune_name, crop_variety,
               n_alert, n_sensor_issue, n_watch, array_to_string(reasons, ', ') as reasons
        from marts.fct_apiary_visit_priority_daily
        where date_local = $1 and priority_rank is not null
          and ($2 is null or beekeeper_id = $2)
        order by beekeeper_id, priority_rank
    """, [day, beekeeper], path)


def apiary_hives(day: date, apiary_id: str, path: Path | None = None) -> pd.DataFrame:
    """Every hive of one apiary on one day, problems first."""
    return _df("""
        select hive_id, status, array_to_string(status_reasons, ', ') as reasons,
               round(data_completeness, 3)   as completeness,
               round(weight_end_kg, 1)       as weight_kg,
               round(weight_delta_24h_kg, 2) as delta_24h_kg,
               round(max_drop_1h_kg, 2)      as max_drop_1h_kg,
               round(brood_temp_mean_c, 1)   as brood_temp_c,
               round(brood_temp_vs_peers_c, 2) as temp_vs_peers_c,
               round(temp_drift_7d_c, 2)     as drift_7d_c,
               round(battery_min_v, 2)       as battery_v
        from marts.fct_hive_status_daily
        where date_local = $1 and apiary_id = $2
        order by case status when 'SENSOR_DOWN' then 1 when 'STALE' then 2
                             when 'SENSOR_DRIFT' then 3 when 'ALERT' then 4
                             when 'WATCH' then 5 else 6 end, hive_id
    """, [day, apiary_id], path)


def hive_history(hive_id: str, end_day: date, days: int = 21,
                 path: Path | None = None) -> pd.DataFrame:
    """Daily trend of one hive up to end_day (weight, brood temperature, rain)."""
    return _df("""
        select date_local, status, weight_end_kg, brood_temp_mean_c, rain_mm,
               data_completeness
        from marts.fct_hive_status_daily
        where hive_id = $1 and date_local between $2 - $3::integer and $2
        order by date_local
    """, [hive_id, end_day, days], path)


def rule_evaluation(path: Path | None = None) -> pd.DataFrame:
    return _df("select * from evaluation.eval_alert_rules", path=path)
