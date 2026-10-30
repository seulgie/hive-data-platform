"""S4 (+ S7 seed): synthetic hive and sensor registry, built on the S3 apiary registry.

Simulates the field-ops system that tracks which sensor is mounted on which hive:

- hives:              one row per hive (from apiaries.n_hives), with its installation time.
- sensor_assignments: SCD Type 2 history "sensor S was on hive H during [valid_from, valid_to)".
                      Telemetry is keyed by sensor_id, so readings reach the right hive only
                      through this table (see resolve_hive).
- fault_log:          ground truth of injected events (S7). Never a mart input; used to
                      generate telemetry (S6) and to score the alert rules.

Business assumptions:
- Season defined in local time (Europe/Paris): 2025-03-01 to 2025-10-31, stored in UTC.
  Intervals are half-open [start, end): an instant belongs to exactly one interval.
- Each apiary is installed on one day within the first 14 days of the season, 07:00-12:00 local.
- 5% of hives lose their sensor once: the sensor fails at any time (DROPOUT, no data), the
  beekeeper swaps it 1-5 days later during working hours, 09:00-17:00 local (SENSOR_SWAP).
  The old assignment ends at the swap.

Run:  python -m hive_platform.synthetic.hives --seed 42
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from hive_platform.ingest.geo.storage import write_json_atomic
from hive_platform.synthetic.apiaries import ApiaryConfig

log = logging.getLogger(__name__)

LOCAL_TZ = "Europe/Paris"
SOURCE_NAME = "synthetic_hive_registry"
GENERATOR_VERSION = "1"


class HiveRegistryError(RuntimeError):
    """The generated registry breaks one of its own invariants."""


@dataclass
class HiveConfig:
    seed: int = 42                       # same seed as the apiary registry it builds on
    season_start_local: str = "2025-03-01"
    season_end_local: str = "2025-11-01"  # exclusive: the season ends at the end of Oct 31
    install_window_days: int = 14
    install_hours_local: tuple[int, int] = (7, 12)
    swap_rate: float = 0.05
    swap_delay_days: tuple[int, int] = (1, 5)    # beekeeper visits 1-5 days after the failure
    swap_hours_local: tuple[int, int] = (9, 17)  # ...during working hours
    failure_margin_days: int = 7         # no failure in the first/last week of a hive's season
    apiary: ApiaryConfig = field(default_factory=ApiaryConfig)

    def __post_init__(self):
        self.apiary.seed = self.seed     # one seed for the whole synthetic world

    @property
    def season_start_utc(self) -> pd.Timestamp:
        return pd.Timestamp(self.season_start_local, tz=LOCAL_TZ).tz_convert("UTC")

    @property
    def season_end_utc(self) -> pd.Timestamp:
        return pd.Timestamp(self.season_end_local, tz=LOCAL_TZ).tz_convert("UTC")

    @property
    def out_dir(self) -> Path:
        geo = self.apiary.geo
        return (geo.raw_root / "synthetic" / "hives"
                / f"dept_code={geo.dept_code}" / f"seed={self.seed}")


@dataclass
class HiveRegistryStats:
    apiaries: int = 0
    hives: int = 0
    sensors: int = 0
    assignments: int = 0
    swaps: int = 0
    install_first_utc: str = ""
    install_last_utc: str = ""


def make_rngs(seed: int) -> dict[str, np.random.Generator]:
    """Independent streams, distinct from the apiary generator's (different spawn key)."""
    names = ["install", "swaps", "failure_time", "swap_delay"]
    children = np.random.SeedSequence(seed, spawn_key=(4,)).spawn(len(names))
    return {name: np.random.default_rng(child) for name, child in zip(names, children)}


# ---------------------------------------------------------------------------
# Steps (pure functions)
# ---------------------------------------------------------------------------

def install_times(apiaries: pd.DataFrame, cfg: HiveConfig,
                  rng: np.random.Generator) -> pd.Series:
    """One installation instant per apiary: a day in the window, a time in working hours (local)."""
    day = pd.Timestamp(cfg.season_start_local)
    offsets_days = rng.integers(0, cfg.install_window_days, size=len(apiaries))
    lo, hi = cfg.install_hours_local
    offsets_min = rng.integers(lo * 60, hi * 60, size=len(apiaries))
    local = [day + pd.Timedelta(days=int(d), minutes=int(m)) for d, m in zip(offsets_days, offsets_min)]
    utc = pd.DatetimeIndex(local).tz_localize(LOCAL_TZ).tz_convert("UTC")
    return pd.Series(utc, index=apiaries.index, name="installed_at_utc")


def build_hives(apiaries: pd.DataFrame, installed_at: pd.Series) -> pd.DataFrame:
    """Expand apiaries into hives: AP-001 with 11 hives -> AP-001-H01 ... AP-001-H11."""
    rows = [
        {"hive_id": f"{a.apiary_id}-H{k:02d}", "apiary_id": a.apiary_id,
         "installed_at_utc": installed_at[i]}
        for i, a in apiaries.sort_values("apiary_id").iterrows()
        for k in range(1, int(a.n_hives) + 1)
    ]
    return pd.DataFrame(rows)


def build_assignments(hives: pd.DataFrame, cfg: HiveConfig,
                      rngs: dict[str, np.random.Generator]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Initial sensor per hive, plus failure -> swap events for a fixed share of hives."""
    end = cfg.season_end_utc
    n_swaps = round(cfg.swap_rate * len(hives))
    swapped = set(rngs["swaps"].choice(hives["hive_id"].to_numpy(), size=n_swaps, replace=False))

    assignments, faults = [], []
    next_sensor = 1

    def new_sensor() -> str:
        nonlocal next_sensor
        sid = f"SN-{next_sensor:05d}"
        next_sensor += 1
        return sid

    margin = pd.Timedelta(days=cfg.failure_margin_days)
    dlo, dhi = cfg.swap_delay_days
    hlo, hhi = cfg.swap_hours_local
    for h in hives.itertuples():          # hives are sorted by hive_id -> stable sensor ids
        first = new_sensor()
        if h.hive_id not in swapped:
            assignments.append((first, h.hive_id, h.installed_at_utc, pd.NaT, "install"))
            continue

        span = (end - margin) - (h.installed_at_utc + margin)
        fail_at = (h.installed_at_utc + margin
                   + pd.Timedelta(seconds=int(rngs["failure_time"].integers(0, span.total_seconds()))))
        # Swap on a later local day, inside working hours
        fail_day_local = fail_at.tz_convert(LOCAL_TZ).normalize().tz_localize(None)
        swap_local = (fail_day_local
                      + pd.Timedelta(days=int(rngs["swap_delay"].integers(dlo, dhi + 1)))
                      + pd.Timedelta(minutes=int(rngs["swap_delay"].integers(hlo * 60, hhi * 60))))
        swap_at = swap_local.tz_localize(LOCAL_TZ).tz_convert("UTC")
        second = new_sensor()
        assignments.append((first, h.hive_id, h.installed_at_utc, swap_at, "install"))
        assignments.append((second, h.hive_id, swap_at, pd.NaT, "swap"))
        faults.append(("DROPOUT", h.hive_id, first, fail_at, swap_at))
        faults.append(("SENSOR_SWAP", h.hive_id, second, swap_at, swap_at))

    assign = pd.DataFrame(assignments, columns=["sensor_id", "hive_id", "valid_from_utc",
                                                "valid_to_utc", "reason"])
    assign["valid_to_utc"] = pd.to_datetime(assign["valid_to_utc"], utc=True)
    assign.insert(0, "assignment_id", [f"AS-{i + 1:05d}" for i in range(len(assign))])

    fault = pd.DataFrame(faults, columns=["fault_type", "hive_id", "sensor_id", "start_utc", "end_utc"])
    fault = fault.sort_values(["start_utc", "fault_type"]).reset_index(drop=True)
    fault.insert(0, "event_id", [f"EV-{i + 1:05d}" for i in range(len(fault))])
    return assign, fault


def resolve_hive(readings: pd.DataFrame, assignments: pd.DataFrame) -> pd.DataFrame:
    """SCD2 point-in-time join: attach hive_id to readings keyed by (sensor_id, ts_utc).

    A reading belongs to the assignment with valid_from <= ts < valid_to (open end = still
    mounted). Readings matching no assignment get hive_id = NA instead of being dropped, so
    the caller sees them.
    """
    rows = readings.assign(_row=np.arange(len(readings)))
    merged = rows.merge(
        assignments[["sensor_id", "hive_id", "valid_from_utc", "valid_to_utc"]],
        on="sensor_id", how="left")
    inside = (merged["ts_utc"] >= merged["valid_from_utc"]) & (
        merged["valid_to_utc"].isna() | (merged["ts_utc"] < merged["valid_to_utc"]))
    matched = merged[inside].set_index("_row")["hive_id"]
    out = readings.copy()
    out["hive_id"] = pd.Series(matched, dtype="string").reindex(np.arange(len(readings))).to_numpy()
    return out


def validate_registry(apiaries: pd.DataFrame, hives: pd.DataFrame, assign: pd.DataFrame,
                      faults: pd.DataFrame, cfg: HiveConfig) -> None:
    problems = []
    start, end = cfg.season_start_utc, cfg.season_end_utc

    expected = apiaries.set_index("apiary_id")["n_hives"]
    actual = hives.groupby("apiary_id").size().reindex(expected.index, fill_value=0)
    if not (expected == actual).all():
        problems.append("hive counts differ from apiaries.n_hives")
    if not hives["hive_id"].is_unique:
        problems.append("duplicate hive_id")
    if not hives["installed_at_utc"].between(start, end, inclusive="left").all():
        problems.append("installation outside the season")

    # A sensor is never reused, so each sensor_id appears in exactly one assignment
    if not assign["sensor_id"].is_unique:
        problems.append("sensor_id reused across assignments")

    # Per hive: intervals start at installation, chain without gap or overlap, last one open
    for hive_id, g in assign.sort_values("valid_from_utc").groupby("hive_id"):
        installed = hives.loc[hives["hive_id"] == hive_id, "installed_at_utc"].iloc[0]
        if g["valid_from_utc"].iloc[0] != installed:
            problems.append(f"{hive_id}: first sensor not mounted at installation")
        if pd.notna(g["valid_to_utc"].iloc[-1]):
            problems.append(f"{hive_id}: no open assignment at season end")
        if g["valid_to_utc"].iloc[:-1].isna().any():
            problems.append(f"{hive_id}: open assignment before the last one")
        if not (g["valid_to_utc"].iloc[:-1].to_numpy() == g["valid_from_utc"].iloc[1:].to_numpy()).all():
            problems.append(f"{hive_id}: gap or overlap between assignments")
    if set(hives["hive_id"]) - set(assign["hive_id"]):
        problems.append("hive without any sensor")

    swaps = faults[faults["fault_type"] == "SENSOR_SWAP"]
    boundaries = set(zip(assign["hive_id"], assign["valid_from_utc"]))
    if not all((h, t) in boundaries for h, t in zip(swaps["hive_id"], swaps["start_utc"])):
        problems.append("SENSOR_SWAP event without matching assignment")
    if not (faults["end_utc"] < end).all():
        problems.append("fault ends after the season")

    if problems:
        raise HiveRegistryError("hive registry validation failed: " + "; ".join(problems[:10]))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def generate(apiaries: pd.DataFrame, cfg: HiveConfig, run_id: str = "test"):
    """Pure core: apiary registry in, (hives, assignments, faults, stats) out."""
    rngs = make_rngs(cfg.seed)
    apiaries = apiaries.sort_values("apiary_id").reset_index(drop=True)

    installed = install_times(apiaries, cfg, rngs["install"])
    hives = build_hives(apiaries, installed)
    assign, faults = build_assignments(hives, cfg, rngs)
    validate_registry(apiaries, hives, assign, faults, cfg)

    for df in (hives, assign, faults):
        df["source"] = SOURCE_NAME
        df["seed"] = cfg.seed
        df["run_id"] = run_id

    stats = HiveRegistryStats(
        apiaries=len(apiaries), hives=len(hives), sensors=assign["sensor_id"].nunique(),
        assignments=len(assign), swaps=int((faults["fault_type"] == "SENSOR_SWAP").sum()),
        install_first_utc=hives["installed_at_utc"].min().isoformat(),
        install_last_utc=hives["installed_at_utc"].max().isoformat(),
    )
    return hives, assign, faults, stats


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def run(cfg: HiveConfig) -> HiveRegistryStats:
    run_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc)
    apiaries_path = cfg.apiary.out_dir / "apiaries.parquet"
    apiaries = pd.DataFrame(gpd.read_parquet(apiaries_path).drop(columns="geometry"))

    hives, assign, faults, stats = generate(apiaries, cfg, run_id)

    write_parquet_atomic(hives, cfg.out_dir / "hives.parquet")
    write_parquet_atomic(assign, cfg.out_dir / "sensor_assignments.parquet")
    write_parquet_atomic(faults, cfg.out_dir / "fault_log.parquet")
    config = asdict(cfg)
    config["apiary"] = {"seed": cfg.apiary.seed, "input": str(apiaries_path)}
    write_json_atomic(cfg.out_dir / "manifest.json", {
        "run_id": run_id,
        "generator": "synthetic.hives",
        "generator_version": GENERATOR_VERSION,
        "generated_at_utc": started.isoformat(),
        "season_utc": [cfg.season_start_utc.isoformat(), cfg.season_end_utc.isoformat()],
        "config": config,
        "stats": asdict(stats),
    })
    log.info("hive registry: %s", json.dumps(asdict(stats)))
    log.info("written to %s", cfg.out_dir)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic hive/sensor registry (S4).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(HiveConfig(seed=args.seed))


if __name__ == "__main__":
    main()
