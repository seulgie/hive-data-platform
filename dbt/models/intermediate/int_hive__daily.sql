-- One row per hive and local day (grain of the Field Ops board).
-- Base daily metrics only; rolling windows and status rules come in the marts.

with spine as (
    select * from {{ ref('int_hive_days') }}
),

daily_readings as (
    select
        hive_id,
        date_local,
        count(*)                                   as n_readings,
        count(distinct sensor_id)                  as n_sensors,
        min(ts_utc)                                as first_reading_utc,
        max(ts_utc)                                as last_reading_utc,
        arg_min(weight_kg, ts_utc)                 as weight_start_kg,
        arg_max(weight_kg, ts_utc)                 as weight_end_kg,
        greatest(0, -min(weight_change_1h_kg))     as max_drop_1h_kg,   -- worst 1 h drop, >= 0
        avg(temp_internal_c)                       as brood_temp_mean_c,
        stddev_samp(temp_internal_c)               as brood_temp_std_c,
        avg(humidity_pct)                          as humidity_mean_pct,
        min(battery_v)                             as battery_min_v
    from {{ ref('int_readings__with_hive') }}
    group by hive_id, date_local
),

weather as (
    select * from {{ ref('int_weather__daily') }}
)

select
    s.hive_id,
    s.apiary_id,
    s.date_local,
    s.expected_readings,
    coalesce(r.n_readings, 0)                               as n_readings,
    coalesce(r.n_readings, 0) / nullif(s.expected_readings, 0)
                                                            as data_completeness,
    coalesce(r.n_sensors, 0)                                as n_sensors,
    r.first_reading_utc,
    r.last_reading_utc,
    r.weight_start_kg,
    r.weight_end_kg,
    r.max_drop_1h_kg,
    r.brood_temp_mean_c,
    r.brood_temp_std_c,
    r.humidity_mean_pct,
    r.battery_min_v,
    w.rain_mm,
    w.temp_ext_mean_c,
    w.is_dry_day
from spine as s
left join daily_readings as r
    on r.hive_id = s.hive_id and r.date_local = s.date_local
left join weather as w
    on w.apiary_id = s.apiary_id and w.date_local = s.date_local
