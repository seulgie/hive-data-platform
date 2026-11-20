-- Paris switches to summer time on 30 Mar 2025 (23 h day) and back on 26 Oct 2025 (25 h day).
-- For hives installed before those days: 92 and 100 slots of 15 min, 96 on other full days.
with full_days as (
    select d.date_local, d.expected_readings
    from {{ ref('int_hive_days') }} as d
    join {{ ref('stg_registry__hives') }} as h using (hive_id)
    where d.date_local > {{ to_local_date('h.installed_at_utc') }}
)
select *
from full_days
where expected_readings <> case date_local
        when date '2025-03-30' then 92
        when date '2025-10-26' then 100
        else 96
    end
