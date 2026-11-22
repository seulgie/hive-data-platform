-- One row per hive, with the sensor mounted on it at the end of the season.
select
    h.hive_id,
    h.apiary_id,
    h.installed_at_utc,
    a.sensor_id                 as current_sensor_id,
    count(*) over (partition by h.hive_id) - 1 as n_sensor_swaps
from {{ ref('stg_registry__hives') }} as h
join {{ ref('stg_registry__sensor_assignments') }} as a using (hive_id)
qualify a.is_current
