"""A tiny warehouse with the mart tables the board reads (3 apiaries, 2 days)."""
from __future__ import annotations

import duckdb
import pytest

DDL = """
create schema marts;
create schema evaluation;

create table marts.dim_apiary as
select * from (values
    ('AP-001', 'BK-1', 'Moissac',   'apple', 44.10, 1.08),
    ('AP-002', 'BK-1', 'Montauban', 'kiwi',  44.02, 1.35),
    ('AP-003', 'BK-2', 'Albias',    'plum',  44.08, 1.45)
) t(apiary_id, beekeeper_id, commune_name, crop_variety, lat, lon);

create table marts.fct_hive_status_daily as
select * from (values
    ('AP-001-H01', 'AP-001', date '2025-06-04', 'OK',          [],                    1.0, 50.0,  0.1, 0.5, 34.5,  0.0, 0.0, 3.9, 0.0),
    ('AP-002-H01', 'AP-002', date '2025-06-04', 'ALERT',       ['SWARM_SUSPECTED'],   1.0, 48.0, -2.2, 2.1, 34.5,  0.0, 0.0, 3.9, 0.0),
    ('AP-002-H02', 'AP-002', date '2025-06-04', 'SENSOR_DOWN', ['SENSOR_DOWN'],       0.0, null, null, null, null, null, null, null, 0.0),
    ('AP-003-H01', 'AP-003', date '2025-06-04', 'WATCH',       ['LOW_COMPLETENESS'],  0.7, 55.0,  0.0, 0.4, 34.4, -0.1, 0.0, 3.9, 3.0),
    ('AP-002-H01', 'AP-002', date '2025-06-03', 'OK',          [],                    1.0, 50.2,  0.3, 0.6, 34.5,  0.0, 0.0, 3.9, 0.0)
) t(hive_id, apiary_id, date_local, status, status_reasons, data_completeness, weight_end_kg,
    weight_delta_24h_kg, max_drop_1h_kg, brood_temp_mean_c, brood_temp_vs_peers_c,
    temp_drift_7d_c, battery_min_v, rain_mm);

create table marts.fct_apiary_visit_priority_daily as
select * from (values
    ('AP-001', date '2025-06-04', 'BK-1', 'Moissac',   'apple', 44.10, 1.08, 1, 0, 0, 0, [],                                  0, null),
    ('AP-002', date '2025-06-04', 'BK-1', 'Montauban', 'kiwi',  44.02, 1.35, 2, 1, 1, 0, ['SENSOR_DOWN', 'SWARM_SUSPECTED'],  5, 1),
    ('AP-003', date '2025-06-04', 'BK-2', 'Albias',    'plum',  44.08, 1.45, 1, 0, 0, 1, ['LOW_COMPLETENESS'],                1, 1)
) t(apiary_id, date_local, beekeeper_id, commune_name, crop_variety, lat, lon,
    n_hives, n_alert, n_sensor_issue, n_watch, reasons, priority_score, priority_rank);

create table evaluation.eval_alert_rules as
select 'SWARM_SUSPECTED' as rule, 'SWARM' as fault_type, 25 as events, 25 as events_caught;
"""


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    con.execute(DDL)
    con.close()
    monkeypatch.setenv("HIVE_WAREHOUSE", str(path))
    return path
