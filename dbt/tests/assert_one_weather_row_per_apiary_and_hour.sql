select apiary_id, ts_utc, count(*) as n
from {{ ref('stg_weather__hourly') }}
group by apiary_id, ts_utc
having count(*) > 1
