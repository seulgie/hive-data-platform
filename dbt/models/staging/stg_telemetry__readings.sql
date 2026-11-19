-- One row per sensor reading (15 min), as exported by the IoT platform.
-- Keyed by sensor_id: the hive is only known through the sensor history (SCD2), later.
select
    cast(sensor_id as varchar)          as sensor_id,
    cast(ts_utc as timestamptz)         as ts_utc,
    cast(weight_kg as double)           as weight_kg,
    cast(temp_internal_c as double)     as temp_internal_c,
    cast(humidity_pct as double)        as humidity_pct,
    cast(battery_v as double)           as battery_v
from {{ source('iot', 'readings') }}
