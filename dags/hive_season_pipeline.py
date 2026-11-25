"""Rebuild the 2025 season for the Field Ops board, from public APIs to the published warehouse.

    ingest_geo -> synth_apiaries -> synth_hives ------------> synth_telemetry -> dbt_build -> publish
                               \\-> ingest_weather -------/

Every step already runs by hand from the repo root (`python -m ...`); this DAG only fixes the
order, retries and timeouts. Data moves between tasks through the lake (data/raw, data/lake),
never through XCom: each task reads what the previous one wrote.

Why it is safe to rerun:
- the ingest steps cache raw API responses, so a rerun calls the APIs only for what is missing;
- the synthetic steps are deterministic for a given seed;
- dbt builds into a separate file, and `publish` swaps it in only after every dbt test passed.

The pipeline itself runs in its own virtualenv (HIVE_PYTHON, HIVE_DBT): geopandas and dbt pin
versions Airflow does not share, so they never live in Airflow's environment.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

HIVE_HOME = os.environ.get("HIVE_HOME", "/opt/hive")  # repo root: data/ paths are relative to it
PY = "${HIVE_PYTHON:-python}"
DBT = "${HIVE_DBT:-dbt}"

# One synthetic world per seed. The dbt vars and the board read seed 42, the module defaults.
SEED = 42

BUILD_DB = "data/lake/_build/warehouse.duckdb"

# Public APIs (IGN WFS, Open-Meteo) fail now and then; the steps are idempotent, so retry.
API_RETRY = {"retries": 2, "retry_delay": timedelta(minutes=5)}
# Local, deterministic steps: a retry would fail the same way, so fail at once.
NO_RETRY = {"retries": 0}

with DAG(
    dag_id="hive_season_pipeline",
    description="Field Ops: geo + weather + synthetic telemetry -> dbt marts -> published warehouse",
    doc_md=__doc__,
    schedule=None,             # the sources are one fixed season: run on demand, not on a clock
    start_date=datetime(2025, 3, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,         # two runs would write the same lake files and build file
    default_args={
        "cwd": HIVE_HOME,
        "execution_timeout": timedelta(hours=1),
        "do_xcom_push": False,  # results live in the lake; no stdout line in the metadata DB
    },
    tags=["field-ops", "v1"],
) as dag:
    ingest_geo = BashOperator(
        task_id="ingest_geo",
        doc_md="S1 RPG parcels + S2 communes (IGN WFS) -> silver GeoParquet.",
        bash_command=f"{PY} -m hive_platform.ingest.geo",
        **API_RETRY,
    )
    synth_apiaries = BashOperator(
        task_id="synth_apiaries",
        doc_md="S3 apiary registry, placed on real parcels.",
        bash_command=f"{PY} -m hive_platform.synthetic.apiaries",
        **NO_RETRY,
    )
    synth_hives = BashOperator(
        task_id="synth_hives",
        doc_md="S4 hives and sensor history (SCD2).",
        bash_command=f"{PY} -m hive_platform.synthetic.hives",
        **NO_RETRY,
    )
    ingest_weather = BashOperator(
        task_id="ingest_weather",
        doc_md="S5 hourly weather for every apiary (Open-Meteo archive).",
        bash_command=f"{PY} -m hive_platform.ingest.weather --seed {SEED}",
        **API_RETRY,
    )
    synth_telemetry = BashOperator(
        task_id="synth_telemetry",
        doc_md="S6 hourly hive telemetry with injected faults (needs hives and weather).",
        bash_command=f"{PY} -m hive_platform.synthetic.telemetry --seed {SEED}",
        **NO_RETRY,
    )
    dbt_build = BashOperator(
        task_id="dbt_build",
        doc_md="Models + tests into a fresh build file. A failed test stops the run here.",
        bash_command=(
            f"set -euo pipefail\n"
            f"rm -f {BUILD_DB} {BUILD_DB}.wal\n"
            f"mkdir -p $(dirname {BUILD_DB})\n"
            f"cd dbt\n"
            f"DBT_DUCKDB_PATH=../{BUILD_DB} {DBT} build --profiles-dir . "
            f"--vars '{{seed: {SEED}}}'"
        ),
        **NO_RETRY,
    )
    publish = BashOperator(
        task_id="publish_warehouse",
        doc_md="Audit the build and swap it in as the live warehouse the board reads.",
        bash_command=f"{PY} -m hive_platform.ops.publish --build {BUILD_DB}",
        **NO_RETRY,
    )

    ingest_geo >> synth_apiaries >> [synth_hives, ingest_weather]
    [synth_hives, ingest_weather] >> synth_telemetry >> dbt_build >> publish
