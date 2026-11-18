"""signals.py and faults.py: pure functions on small hand-made inputs."""
import numpy as np
import pandas as pd
import pytest

from hive_platform.synthetic.telemetry import faults as flt
from hive_platform.synthetic.telemetry import signals as sig
from hive_platform.synthetic.telemetry.config import TelemetryConfig

UTC = "UTC"


def one_day(day="2025-04-20", temp=20.0, rain=0.0):
    """96 readings of one local day with constant weather."""
    start = pd.Timestamp(day, tz=sig.LOCAL_TZ).tz_convert(UTC)
    ts = sig.time_grid(start, start + pd.Timedelta(days=1), 15)
    wx = pd.DataFrame({"temperature_2m_c": temp, "precipitation_mm": rain}, index=ts)
    return ts, wx


# ---------------------------------------------------------------------------
# Healthy signals
# ---------------------------------------------------------------------------

def test_time_grid_is_aligned_and_half_open():
    ts = sig.time_grid(pd.Timestamp("2025-03-01 07:07", tz=UTC),
                       pd.Timestamp("2025-03-01 08:00", tz=UTC), 15)
    assert list(ts.strftime("%H:%M")) == ["07:15", "07:30", "07:45"]


def test_bees_fly_only_by_day_and_in_good_weather():
    ts, dry = one_day()
    hours = sig.local_hour(ts)
    activity = sig.forage_activity(ts, dry)
    assert activity[(hours < 8) | (hours >= 19)].max() == 0
    assert activity.max() > 0.95

    _, rainy = one_day(rain=1.0)
    _, cold = one_day(temp=8.0)
    assert sig.forage_activity(ts, rainy).max() == 0
    assert sig.forage_activity(ts, cold).max() == 0


def test_bloom_window_follows_the_crop():
    ts, _ = one_day("2025-04-20")
    assert sig.in_bloom(ts, "apple", 2025).all()       # apple: 10 Apr - 10 May
    assert not sig.in_bloom(ts, "kiwi", 2025).any()    # kiwi: 15 May - 15 Jun


def test_a_full_flying_day_brings_exactly_the_daily_gain():
    ts, wx = one_day()
    activity = sig.forage_activity(ts, wx)
    assert activity.sum() == pytest.approx(sig.full_day_activity(15))


def test_dry_days_have_a_daytime_dip_rainy_days_do_not():
    params = sig.HiveParams(45, 0.8, 1.0, 0.2, 0.2, 0.06)

    def daily_range(rain):
        ts, wx = one_day(rain=rain)
        w = sig.healthy_signals(ts, wx, "apple", 2025, params, 15, np.random.default_rng(0))
        return w["weight_kg"].max() - w["weight_kg"].min()

    assert daily_range(0.0) > 0.6
    assert daily_range(1.0) < 0.3


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------

def test_swarm_drops_weight_within_one_hour_and_stays_lower():
    ts = pd.date_range("2025-05-10 10:00", periods=12, freq="15min", tz=UTC)
    start = pd.Timestamp("2025-05-10 11:00", tz=UTC)
    w = flt.apply_swarm(ts, np.full(12, 50.0), start, 2.0)
    assert (w[ts <= start] == 50.0).all()
    assert w[ts == start + pd.Timedelta(hours=1)][0] == pytest.approx(48.0)
    assert (w[ts >= start + pd.Timedelta(hours=1)] == pytest.approx(48.0))


def test_drift_ramps_then_stays():
    start = pd.Timestamp("2025-06-01", tz=UTC)
    ts = pd.DatetimeIndex([start - pd.Timedelta(days=1), start + pd.Timedelta(days=7),
                           start + pd.Timedelta(days=21), start + pd.Timedelta(days=40)])
    t = flt.apply_drift(ts, np.full(4, 34.5), start, 3.0, 21)
    assert t == pytest.approx([34.5, 35.5, 37.5, 37.5])


def test_faults_are_drawn_among_hives_without_swap_inside_their_windows():
    cfg = TelemetryConfig()
    hives = pd.DataFrame({"hive_id": [f"H{i:03d}" for i in range(200)]})
    assigns = pd.DataFrame({"hive_id": hives["hive_id"], "sensor_id": [f"S{i}" for i in range(200)],
                            "reason": ["swap" if i < 20 else "install" for i in range(200)]})

    f = flt.draw_faults(hives, assigns, cfg, np.random.default_rng(0))

    assert (f["fault_type"] == "SWARM").sum() == round(0.05 * 200)
    assert (f["fault_type"] == "DRIFT").sum() == round(0.03 * 200)
    assert not set(f["hive_id"]) & {f"H{i:03d}" for i in range(20)}   # no swapped hive
    swarm = f[f["fault_type"] == "SWARM"]["start_utc"].dt.tz_convert(sig.LOCAL_TZ)
    assert swarm.dt.month.isin([5, 6]).all() and swarm.dt.hour.between(11, 14).all()
    pd.testing.assert_frame_equal(f, flt.draw_faults(hives, assigns, cfg, np.random.default_rng(0)))
