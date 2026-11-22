-- Which apiaries should each beekeeper visit, in which order? One row per apiary and day.
-- Score weights: an alert needs a visit today; a sensor problem needs a visit soon;
-- a watch is worth a look if the beekeeper is nearby.

with hive_status as (
    select * from {{ ref('fct_hive_status_daily') }}
),

apiaries as (
    select * from {{ ref('dim_apiary') }}
),

per_apiary as (
    select
        apiary_id,
        date_local,
        count(*)                                                          as n_hives,
        count(*) filter (where status = 'ALERT')                          as n_alert,
        count(*) filter (where status in ('SENSOR_DOWN', 'STALE', 'SENSOR_DRIFT'))
                                                                          as n_sensor_issue,
        count(*) filter (where status = 'WATCH')                          as n_watch,
        list_sort(list_distinct(flatten(list(status_reasons))))           as reasons
    from hive_status
    group by apiary_id, date_local
),

scored as (
    select
        p.*,
        3 * n_alert + 2 * n_sensor_issue + 1 * n_watch                    as priority_score,
        a.beekeeper_id,
        a.commune_name,
        a.crop_variety,
        a.lat,
        a.lon
    from per_apiary as p
    join apiaries as a using (apiary_id)
)

select
    *,
    case when priority_score > 0 then
        row_number() over (
            partition by beekeeper_id, date_local
            order by priority_score desc, n_alert desc, apiary_id
        )
    end as priority_rank          -- per beekeeper and day; null = nothing to do there
from scored
