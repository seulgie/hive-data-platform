-- A hive with a sensor problem must never be reported as ALERT or WATCH:
-- its weight and temperature cannot be trusted.
select hive_id, date_local, status, status_reasons
from {{ ref('fct_hive_status_daily') }}
where (list_contains(status_reasons, 'SENSOR_DOWN') and status <> 'SENSOR_DOWN')
   or (list_contains(status_reasons, 'STALE') and status not in ('SENSOR_DOWN', 'STALE'))
