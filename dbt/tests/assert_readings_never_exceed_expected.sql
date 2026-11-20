-- More readings than 15-min slots in a local day means a time-zone or day-boundary bug.
select hive_id, date_local, n_readings, expected_readings
from {{ ref('int_hive__daily') }}
where n_readings > expected_readings
   or data_completeness < 0
