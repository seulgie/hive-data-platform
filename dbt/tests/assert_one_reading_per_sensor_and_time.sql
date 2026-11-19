-- Grain check for the biggest table: (sensor_id, ts_utc) must be unique.
-- A singular test passes when it returns no rows.
select sensor_id, ts_utc, count(*) as n
from {{ ref('stg_telemetry__readings') }}
group by sensor_id, ts_utc
having count(*) > 1
