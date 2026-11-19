-- One row per apiary and UTC hour. The grid point is the weather model's, not the apiary's.
select
    cast(apiary_id as varchar)              as apiary_id,
    cast(ts_utc as timestamptz)             as ts_utc,
    cast(grid_lat as double)                as grid_lat,
    cast(grid_lon as double)                as grid_lon,
    cast(temperature_2m_c as double)        as temperature_2m_c,
    cast(precipitation_mm as double)        as precipitation_mm,
    cast(wind_speed_10m_kmh as double)      as wind_speed_10m_kmh
from {{ source('weather', 'weather_hourly') }}
