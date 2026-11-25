# One image for every service: Airflow, plus the pipeline in its own virtualenv.
#
# Why two Python environments in one image: Airflow pins hundreds of packages (its
# constraints file), and so do geopandas and dbt. Installed together they fight over versions.
# Airflow only needs to start commands, so the pipeline gets a separate virtualenv and the DAG
# calls it by path (HIVE_PYTHON, HIVE_DBT).
FROM apache/airflow:3.3.2-python3.11

USER root
RUN python -m venv /opt/hive-venv \
 && mkdir -p /opt/hive \
 && chown -R airflow:0 /opt/hive-venv /opt/hive
USER airflow

# Dependencies first, the package last: a code change rebuilds only the last layers.
# PIP_USER=false: the Airflow image turns on --user installs, which a virtualenv refuses.
COPY --chown=airflow:0 docker/requirements-pipeline.txt /tmp/requirements-pipeline.txt
RUN PIP_USER=false /opt/hive-venv/bin/pip install --no-cache-dir -r /tmp/requirements-pipeline.txt

COPY --chown=airflow:0 pyproject.toml /opt/hive/pyproject.toml
COPY --chown=airflow:0 src /opt/hive/src
COPY --chown=airflow:0 dbt /opt/hive/dbt
# Editable: docker-compose mounts the repo over /opt/hive, and the code you edit is the code
# that runs. Without the mount, the image still works on the copy baked in above.
RUN PIP_USER=false /opt/hive-venv/bin/pip install --no-cache-dir --no-deps -e /opt/hive

COPY --chown=airflow:0 dags /opt/airflow/dags

ENV HIVE_HOME=/opt/hive \
    HIVE_PYTHON=/opt/hive-venv/bin/python \
    HIVE_DBT=/opt/hive-venv/bin/dbt
