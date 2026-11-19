-- Ground truth of injected events (S4 + S6). Used only to evaluate alert rules:
-- no mart may select from this model.
select
    cast(event_id as varchar)               as event_id,
    cast(fault_type as varchar)             as fault_type,
    cast(hive_id as varchar)                as hive_id,
    cast(sensor_id as varchar)              as sensor_id,
    cast(start_utc as timestamptz)          as start_utc,
    cast(end_utc as timestamptz)            as end_utc,
    cast(magnitude as double)               as magnitude
from {{ source('iot', 'fault_log') }}
