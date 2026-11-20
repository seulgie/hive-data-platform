-- Weather per apiary and local day. A "dry day" is what the alert rules need to tell
-- a rainy-day weight loss (normal: bees stay home) from a worrying one.

select
    apiary_id,
    {{ to_local_date('ts_utc') }}       as date_local,
    count(*)                            as weather_hours,        -- 23, 24 or 25
    sum(precipitation_mm)               as rain_mm,
    avg(temperature_2m_c)               as temp_ext_mean_c,
    max(temperature_2m_c)               as temp_ext_max_c,
    max(wind_speed_10m_kmh)             as wind_max_kmh,
    sum(precipitation_mm) < 1.0         as is_dry_day
from {{ ref('stg_weather__hourly') }}
group by all
