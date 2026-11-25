import duckdb
import pytest

from hive_platform.ops.publish import REQUIRED_TABLES, PublishError, publish, wal_path


def make_warehouse(path, rows=1, skip=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        for table in REQUIRED_TABLES:
            if table in skip:
                continue
            schema = table.split(".")[0]
            con.execute(f"create schema if not exists {schema}")
            con.execute(f"create table {table} as select range as id from range({rows})")
    return path


def read_ids(path, table="marts.dim_apiary"):
    with duckdb.connect(str(path), read_only=True) as con:
        return con.execute(f"select count(*) from {table}").fetchone()[0]


def test_publish_replaces_live_and_removes_build(tmp_path):
    build = make_warehouse(tmp_path / "_build" / "warehouse.duckdb", rows=5)
    live = make_warehouse(tmp_path / "warehouse.duckdb", rows=1)

    counts = publish(build, live)

    assert counts["marts.dim_apiary"] == 5
    assert read_ids(live) == 5
    assert not build.exists()


def test_first_publish_creates_live(tmp_path):
    build = make_warehouse(tmp_path / "_build" / "warehouse.duckdb")
    live = tmp_path / "lake" / "warehouse.duckdb"

    publish(build, live)

    assert live.exists()


def test_missing_table_keeps_old_live(tmp_path):
    build = make_warehouse(tmp_path / "_build" / "warehouse.duckdb",
                           skip=("evaluation.eval_alert_rules",))
    live = make_warehouse(tmp_path / "warehouse.duckdb", rows=1)

    with pytest.raises(PublishError, match="eval_alert_rules"):
        publish(build, live)
    assert read_ids(live) == 1


def test_empty_table_is_not_published(tmp_path):
    build = make_warehouse(tmp_path / "_build" / "warehouse.duckdb", rows=0)
    live = tmp_path / "warehouse.duckdb"

    with pytest.raises(PublishError, match="empty"):
        publish(build, live)
    assert not live.exists()


def test_no_build_fails_loudly(tmp_path):
    with pytest.raises(PublishError, match="no build"):
        publish(tmp_path / "missing.duckdb", tmp_path / "warehouse.duckdb")


def test_unclosed_build_is_not_published(tmp_path):
    build = make_warehouse(tmp_path / "_build" / "warehouse.duckdb")
    wal_path(build).write_bytes(b"")

    with pytest.raises(PublishError, match="not closed cleanly"):
        publish(build, tmp_path / "warehouse.duckdb")


def test_open_live_warehouse_blocks_publish(tmp_path):
    build = make_warehouse(tmp_path / "_build" / "warehouse.duckdb", rows=5)
    live = make_warehouse(tmp_path / "warehouse.duckdb", rows=1)
    wal_path(live).write_bytes(b"")

    with pytest.raises(PublishError, match="writer has the live warehouse open"):
        publish(build, live)
    assert build.exists()
