-- SCD Type 2: sensor S was mounted on hive H during [valid_from_utc, valid_to_utc).
-- valid_to_utc is null while the sensor is still mounted.
select
    cast(assignment_id as varchar)          as assignment_id,
    cast(sensor_id as varchar)              as sensor_id,
    cast(hive_id as varchar)                as hive_id,
    cast(valid_from_utc as timestamptz)     as valid_from_utc,
    cast(valid_to_utc as timestamptz)       as valid_to_utc,
    cast(reason as varchar)                 as reason,                -- install | swap
    valid_to_utc is null                    as is_current
from {{ source('registry', 'sensor_assignments') }}
