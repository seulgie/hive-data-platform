"""DAG integrity: the file imports, the graph is what the docstring draws, and every command
points at a module that exists. Needs Airflow, so it is skipped in the conda env and runs in
its own CI job (and inside the Docker image)."""
import re
from pathlib import Path

import pytest

dagbag = pytest.importorskip("airflow.dag_processing.dagbag")

REPO = Path(__file__).resolve().parents[2]
DAG_ID = "hive_season_pipeline"

EXPECTED_UPSTREAM = {
    "ingest_geo": set(),
    "synth_apiaries": {"ingest_geo"},
    "synth_hives": {"synth_apiaries"},
    "ingest_weather": {"synth_apiaries"},
    "synth_telemetry": {"synth_hives", "ingest_weather"},
    "dbt_build": {"synth_telemetry"},
    "publish_warehouse": {"dbt_build"},
}


@pytest.fixture(scope="module")
def dag():
    bag = dagbag.DagBag(dag_folder=REPO / "dags")
    assert bag.import_errors == {}
    return bag.dags[DAG_ID]


def test_graph_matches_design(dag):
    assert {t.task_id: set(t.upstream_task_ids) for t in dag.tasks} == EXPECTED_UPSTREAM


def test_one_run_at_a_time_and_on_demand(dag):
    assert dag.max_active_runs == 1
    assert type(dag.timetable).__name__ == "NullTimetable"  # schedule=None: manual runs only
    assert dag.catchup is False


def test_only_api_steps_retry(dag):
    retrying = {t.task_id for t in dag.tasks if t.retries > 0}
    assert retrying == {"ingest_geo", "ingest_weather"}


def test_every_task_has_a_timeout(dag):
    assert all(t.execution_timeout is not None for t in dag.tasks)


def test_every_module_the_dag_runs_exists(dag):
    for task in dag.tasks:
        for module in re.findall(r"-m ([\w.]+)", task.bash_command):
            parts = module.split(".")
            as_package = REPO / "src" / Path(*parts) / "__main__.py"
            as_module = REPO / "src" / Path(*parts[:-1]) / f"{parts[-1]}.py"
            assert as_package.exists() or as_module.exists(), f"{task.task_id}: {module}"


def test_dbt_never_writes_the_live_warehouse(dag):
    build = dag.get_task("dbt_build").bash_command
    assert "DBT_DUCKDB_PATH=../data/lake/_build/" in build
