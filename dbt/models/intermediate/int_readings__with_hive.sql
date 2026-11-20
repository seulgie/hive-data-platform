-- Every sensor reading, attached to the hive the sensor was mounted on at that instant.
--
-- SCD2 point-in-time join, half-open intervals: valid_from <= ts < valid_to
-- (valid_to null = still mounted). Same rule as hive_platform.synthetic.hives.resolve_hive.
-- Left join: a reading matching no assignment keeps hive_id null, and a test fails on it.

with readings as (
    select * from {{ ref('stg_telemetry__readings') }}
),

assignments as (
    select * from {{ ref('stg_registry__sensor_assignments') }}
),

hives as (
    select hive_id, apiary_id from {{ ref('stg_registry__hives') }}
),

joined as (
    select
        r.sensor_id,
        r.ts_utc,
        a.assignment_id,
        a.hive_id,
        h.apiary_id,
        r.weight_kg,
        r.temp_internal_c,
        r.humidity_pct,
        r.battery_v
    from readings as r
    left join assignments as a
        on  a.sensor_id = r.sensor_id
        and r.ts_utc >= a.valid_from_utc
        and (a.valid_to_utc is null or r.ts_utc < a.valid_to_utc)
    left join hives as h
        on h.hive_id = a.hive_id
)

select
    *,
    {{ to_local_date('ts_utc') }} as date_local,

    -- Change against the heaviest weight of the previous hour (<= 0 means a drop).
    -- A RANGE frame works on time, not on row counts: missing readings do not shift it.
    -- Partitioned by hive, so a sensor swap does not break the series.
    weight_kg - max(weight_kg) over (
        partition by hive_id
        order by ts_utc
        range between interval 1 hour preceding and current row
    ) as weight_change_1h_kg
from joined
