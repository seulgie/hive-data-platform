-- Spine: one row per hive and local day, from its installation day to the end of the season,
-- whether or not any reading arrived. Days without data must exist to be flagged
-- (a dead sensor is a missing day, not a missing row).
--
-- expected_readings = 15-minute slots of that local day after installation.
-- Local days are 23 h (30 Mar 2025) or 25 h (26 Oct 2025) around DST changes:
-- day bounds are computed as local midnights converted to UTC, never as date + 24 h.

with hives as (
    select hive_id, apiary_id, installed_at_utc
    from {{ ref('stg_registry__hives') }}
),

days as (
    select
        h.hive_id,
        h.apiary_id,
        h.installed_at_utc,
        cast(d.day as date) as date_local
    from hives as h,
    unnest(generate_series(
        {{ to_local_date('h.installed_at_utc') }},
        cast('{{ var("season_end_local") }}' as date) - 1,
        interval 1 day
    )) as d(day)
),

bounds as (
    select
        *,
        {{ local_midnight_utc('date_local') }}     as day_start_utc,
        {{ local_midnight_utc('date_local + 1') }} as day_end_utc
    from days
)

select
    hive_id,
    apiary_id,
    date_local,
    day_start_utc,
    day_end_utc,
    -- first 15-min slot at or after max(day start, installation), up to day end (exclusive)
    cast(
        (epoch(day_end_utc)
         - ceil(epoch(greatest(day_start_utc, installed_at_utc)) / 900) * 900) / 900
    as integer) as expected_readings
from bounds
