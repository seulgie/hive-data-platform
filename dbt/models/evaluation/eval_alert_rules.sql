-- How good are the status rules? Scores each rule against the injected ground truth.
-- This folder is the only place allowed to read stg_iot__fault_log.
--
-- recall    = injected events caught by the rule / injected events
-- precision = hive-days flagged by the rule that match an event / hive-days flagged
-- delay     = days between the event start and the first matching flag
-- decisive  = flagged hive-days where this rule's status won (what the beekeeper sees)
--
-- BROOD_TEMP has no injected fault in v1 (BROOD_LOSS is deferred): every day it decides
-- is a false alert by construction, which is exactly what we want to see.

with status as (
    select hive_id, date_local, status, status_reasons
    from {{ ref('fct_hive_status_daily') }}
),

events as (
    select
        event_id,
        fault_type,
        hive_id,
        {{ to_local_date('start_utc') }}                   as start_date,
        {{ to_local_date('end_utc - interval 1 second') }} as end_date
    from {{ ref('stg_iot__fault_log') }}
    where fault_type in ('SWARM', 'DROPOUT', 'DRIFT', 'BROOD_LOSS')
),

-- rule -> fault it is meant to catch, and the day window that counts as a match
rules (rule, fault_type, rule_status, window_after_days) as (
    values
        ('SWARM_SUSPECTED', 'SWARM',      'ALERT',        0),     -- same day
        ('BROOD_TEMP',      'BROOD_LOSS', 'ALERT',        0),
        ('SENSOR_DOWN',     'DROPOUT',    'SENSOR_DOWN',  0),     -- any day of the outage
        ('STALE',           'DROPOUT',    'STALE',        0),
        ('SENSOR_DRIFT',    'DRIFT',      'SENSOR_DRIFT', NULL)   -- any day after the start
),

flags as (
    select s.hive_id, s.date_local, s.status, unnest(s.status_reasons) as rule
    from status as s
),

matches as (
    select
        f.rule,
        f.hive_id,
        f.date_local,
        e.event_id,
        f.date_local - e.start_date as delay_days,
        f.status = r.rule_status    as is_decisive
    from flags as f
    join rules as r using (rule)
    left join events as e
        on  e.hive_id = f.hive_id
        and e.fault_type = r.fault_type
        and f.date_local >= e.start_date
        and f.date_local <= case when r.window_after_days is null then date '9999-12-31'
                                 else greatest(e.end_date, e.start_date + r.window_after_days)
                            end
),

per_rule as (
    select
        rule,
        count(*)                                            as flagged_hive_days,
        count(*) filter (where event_id is not null)        as true_hive_days,
        count(*) filter (where is_decisive)                 as decisive_hive_days,
        count(*) filter (where is_decisive and event_id is null)
                                                            as false_alert_days,
        count(distinct event_id)                            as events_caught
    from matches
    group by rule
),

first_catch as (
    select rule, event_id, min(delay_days) as delay_days
    from matches
    where event_id is not null
    group by rule, event_id
),

totals as (
    select r.rule, r.fault_type, count(e.event_id) as events
    from rules as r
    left join events as e using (fault_type)
    group by r.rule, r.fault_type
)

select
    t.rule,
    t.fault_type,
    t.events,
    coalesce(p.events_caught, 0)                                        as events_caught,
    round(coalesce(p.events_caught, 0) / nullif(t.events, 0), 3)        as recall,
    coalesce(p.flagged_hive_days, 0)                                    as flagged_hive_days,
    round(p.true_hive_days / nullif(p.flagged_hive_days, 0), 3)         as precision,
    coalesce(p.decisive_hive_days, 0)                                   as decisive_hive_days,
    coalesce(p.false_alert_days, 0)                                     as false_alert_days,
    (select median(delay_days) from first_catch as c where c.rule = t.rule)
                                                                        as median_delay_days
from totals as t
left join per_rule as p using (rule)
order by t.fault_type, t.rule
