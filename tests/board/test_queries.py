"""queries.py: what each board element shows, on a tiny warehouse."""
from datetime import date

import duckdb

from hive_platform.board import queries as q

DAY = date(2025, 6, 4)


def test_season_bounds_and_beekeepers(warehouse):
    assert q.season_bounds() == (date(2025, 6, 3), DAY)
    assert q.beekeepers() == ["BK-1", "BK-2"]


def test_status_counts_filter_by_beekeeper(warehouse):
    all_ = q.status_counts(DAY).set_index("status")["hives"].to_dict()
    bk2 = q.status_counts(DAY, "BK-2").set_index("status")["hives"].to_dict()
    assert all_ == {"OK": 1, "ALERT": 1, "SENSOR_DOWN": 1, "WATCH": 1}
    assert bk2 == {"WATCH": 1}


def test_apiary_need_puts_alert_before_sensor_and_watch(warehouse):
    needs = q.apiary_map(DAY).set_index("apiary_id")["need"].to_dict()
    assert needs == {"AP-001": "OK", "AP-002": "ALERT", "AP-003": "WATCH"}


def test_visit_list_skips_apiaries_without_work(warehouse):
    visits = q.visit_list(DAY)
    assert visits["apiary_id"].tolist() == ["AP-002", "AP-003"]
    assert visits["reasons"].iloc[0] == "SENSOR_DOWN, SWARM_SUSPECTED"


def test_apiary_hives_lists_sensor_problems_first(warehouse):
    hives = q.apiary_hives(DAY, "AP-002")
    assert hives["status"].tolist() == ["SENSOR_DOWN", "ALERT"]


def test_hive_history_is_bounded_and_ordered(warehouse):
    history = q.hive_history("AP-002-H01", DAY, days=1)
    assert history["date_local"].dt.date.tolist() == [date(2025, 6, 3), DAY]


def test_connections_are_closed_so_dbt_can_write(warehouse):
    q.apiary_map(DAY)
    con = duckdb.connect(str(warehouse))          # would fail if a reader kept the file
    con.execute("create table marts.write_check as select 1 as x")
    con.close()
