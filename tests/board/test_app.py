"""Smoke test: the whole Streamlit app runs on a tiny warehouse, without a browser."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "src" / "hive_platform" / "board" / "app.py"


def test_app_renders_kpis_and_tables(warehouse):
    at = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Alert"] == "1" and metrics["Sensor Down"] == "1"
    assert len(at.dataframe) >= 2                    # visit list + apiary detail


def test_app_filters_by_beekeeper(warehouse):
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.selectbox[0].set_value("BK-2").run()

    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Watch"] == "1"
    assert {m.label: m.value for m in at.metric}["Alert"] == "0"
