select
    cast(hive_id as varchar)                as hive_id,
    cast(apiary_id as varchar)              as apiary_id,
    cast(installed_at_utc as timestamptz)   as installed_at_utc
from {{ source('registry', 'hives') }}
