# hive-data-platform

A data platform for a **composite ag-tech company** that runs sensor-equipped beehives
for orchard pollination in Tarn-et-Garonne (France). It is a portfolio case study, built
bottom-up the way a first data engineer would: real public data where it exists,
simulators where a company's internal systems would be. Not affiliated with any company.

**First consumer:** the Field Ops *hive health board*. Every morning it answers the
beekeepers' question: *which apiaries need a visit today, and why?*
([requirements](docs/requirements/field_ops.md), [source mapping](docs/requirements/source_mapping.md))

## Status

| Step | Source | Kind | Status |
|---|---|---|---|
| S1 | Communes + department boundary (geo.api.gouv.fr) | real | ✅ |
| S2 | RPG 2024 agricultural parcels (IGN API Carto) | real | ✅ 99,057 parcels |
| S3 | Apiary registry, placed on real orchard parcels | synthetic | ✅ 40 apiaries, 3 beekeepers |
| S4 | Hive + sensor registry (sensor swaps as SCD2) | synthetic | next |
| S5 | Hourly weather per apiary (Open-Meteo) | real | planned |
| S6 | Hive telemetry, driven by S5 weather | synthetic | planned |
| S7 | Injected fault log (ground truth for alert rules) | synthetic | planned |

## Architecture

```
external APIs ──▶ ingest/ ──▶ data/raw (bronze, immutable cache)
                     │
                     └──────▶ data/lake/silver (validated GeoParquet) + run manifest
                                    │
                synthetic/ ◀────────┘   simulators of internal systems (seeded)
                     │
                     └──────▶ data/raw/synthetic (as if exported by an ops system)
```

- **`ingest/` = facts, `synthetic/` = assumptions.** Real data is cleaned but never
  invented; synthetic data is generated from documented assumptions and a fixed seed, and
  can be replaced by a real export without touching anything downstream.
- **Bronze is immutable.** Every API response is cached on disk; transforms replay from
  the cache with zero API calls (full RPG reprocessing: ~30 s).
- **Fail loudly.** Invariants run inside the pipeline; a broken invariant stops the run
  before anything is written.
- **Traceability.** Every run writes a manifest with its config and row counts per step;
  every row carries `run_id` and its source.

## The RPG ingest in numbers (dept 82, RPG 2024)

```
140,211 returned by 196 tiles (5 km)
 −9,400 cross-tile duplicates      (1 split record merged)
  1,484 invalid geometries repaired (843 became GeometryCollections → polygons kept)
−31,754 outside the department     (membership by representative point; 849 flagged)
 99,057 parcels in silver
```

## Data findings that shaped the design

| Finding | Where | Consequence |
|---|---|---|
| `cat_cult_p` has both real nulls and the literal string `"NA"` | notebook 01 | pandas `string` dtype + Parquet; never round-trip through CSV |
| One tile is not representative (volume, null columns) | notebook 02 | profiled random + edge tiles before the full run |
| ~1% invalid geometries; `make_valid` creates collections | notebooks 01–02 | repair, then keep polygon parts only |
| 39% of an edge tile lies outside the department | notebook 02 | representative-point membership: one parcel, one department |
| A parcel stored as two records (6.44 ha body + 3 m² sliver) | full run | dedupe unions geometries; conflicting attributes still fail the run |
| No PAC code for apple or kiwi: both hide in `VRG` (72% of orchard area) | notebooks 02–03 | registry imputes `crop_variety`, flagged by `crop_variety_source` |

## Quickstart

```bash
conda env create -f environment.yml && conda activate hive
pip install -e .
pytest -q

python -m hive_platform.ingest.geo --dept 82 --year 2024     # S1 + S2, ~7 min first run
python -m hive_platform.synthetic.apiaries --seed 42          # S3, seconds
```

## Repository layout

```
src/hive_platform/
├── ingest/geo/        S1 + S2: client, raw_store, spatial, quality, storage, pipeline, config
└── synthetic/         S3 apiaries.py (S4, S6, S7 to come)
tests/                 one test module per responsibility, no network
notebooks/             01 profiling → 02 pre-ingest checks → 03 silver validation + S3 sizing
scripts/               inspect_parcel.py: every cached copy of one parcel
docs/requirements/     Field Ops requirements, source mapping
```

## Known simplifications

- Hives per apiary are uniform (8–16) and not tied to parcel area; a hives-per-hectare SLA
  (Grower Success board) will need them derived from contracted area.
- `crop_variety` inside `VRG` is imputed (apple 72 / kiwi 15 / other 13), calibrated on
  public orchard statistics, not observed.
- One department, one RPG vintage (2024), one season (2025-03-01 → 2025-10-31).
- Local stack (Python, GeoParquet, DuckDB next). Orchestration (Airflow) and Spark come
  with the refactoring phase, once the flow is stable.

## Data sources

- Communes: [geo.api.gouv.fr](https://geo.api.gouv.fr) (Etalab)
- Parcels: [RPG via IGN API Carto](https://apicarto.ign.fr/api/doc/rpg) (IGN / ASP)
- Crop codes: [Codes cultures PAC](https://www.data.gouv.fr/datasets/codes-cultures-pac) (data.gouv.fr)