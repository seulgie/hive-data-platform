-- Field Ops board, one row per hive and local day: KPIs, status, and why.
--
-- Status priority (a sensor problem makes every other signal untrustworthy):
--   SENSOR_DOWN > STALE > SENSOR_DRIFT > ALERT > WATCH > OK
-- status_reasons lists every rule that fired, not only the winning one.

with daily as (
    select * from {{ ref('int_hive__daily') }}
),

rolling as (
    select
        *,
        -- carried across days without data: when did this hive last talk to us?
        max(last_reading_utc) over w_cumulative                         as last_seen_utc,
        weight_end_kg - lag(weight_end_kg, 1) over w_days               as weight_delta_24h_kg,
        weight_end_kg - lag(weight_end_kg, 7) over w_days               as weight_delta_7d_kg,
        avg(brood_temp_mean_c) over w_7d                                as brood_temp_mean_7d_c
    from daily
    window
        w_days       as (partition by hive_id order by date_local),
        w_cumulative as (partition by hive_id order by date_local
                         rows between unbounded preceding and current row),
        w_7d         as (partition by hive_id order by date_local
                         rows between 6 preceding and current row)
),

with_peers as (
    select
        *,
        -- peer comparison: hives of one apiary share weather and forage, so a hive far
        -- from its neighbours' median points at the sensor, not at the bees
        brood_temp_mean_7d_c
            - median(brood_temp_mean_7d_c) over (partition by apiary_id, date_local)
                                                                        as temp_drift_7d_c,
        brood_temp_mean_c
            - median(brood_temp_mean_c) over (partition by apiary_id, date_local)
                                                                        as brood_temp_vs_peers_c
    from rolling
),

flags as (
    select
        *,
        date_diff('minute', last_seen_utc, day_end_utc) / 60.0          as hours_since_last_reading,
        data_completeness < {{ var('completeness_down') }}              as f_sensor_down,
        last_seen_utc is null
            or date_diff('minute', last_seen_utc, day_end_utc) / 60.0 > {{ var('stale_hours') }}
                                                                        as f_stale,
        coalesce(temp_drift_7d_c > {{ var('drift_c') }}, false)        as f_drift,
        coalesce(max_drop_1h_kg > {{ var('swarm_alert_kg') }}, false)  as f_swarm,
        -- Brood loss: one colony stops regulating and gets colder than its neighbours.
        -- Weather cools every hive of an apiary alike, so compare with the apiary median
        -- (eval_alert_rules: an upper bound gave 216 false alerts on drifting sensors, an
        -- absolute 33 C floor 127 on cold March days). Same principle as SENSOR_DRIFT:
        -- alone and hotter than the neighbours = sensor, alone and colder = bees.
        coalesce(brood_temp_vs_peers_c < -{{ var('brood_cold_vs_peers_c') }}
                 or brood_temp_std_c > {{ var('brood_temp_std_max_c') }}, false)
                                                                        as f_brood,
        coalesce(max_drop_1h_kg > {{ var('swarm_watch_kg') }}, false)  as f_sharp_drop,
        coalesce(is_dry_day and weight_delta_24h_kg < -{{ var('dry_day_loss_kg') }}, false)
                                                                        as f_weight_loss,
        coalesce(weight_delta_7d_kg < -{{ var('decline_7d_kg') }}, false)
                                                                        as f_decline_7d,
        data_completeness < {{ var('completeness_watch') }}             as f_low_completeness
    from with_peers
)

select
    hive_id,
    apiary_id,
    date_local,
    day_end_utc,
    -- data health
    expected_readings,
    n_readings,
    data_completeness,
    last_seen_utc,
    hours_since_last_reading,
    -- weight
    weight_end_kg,
    weight_delta_24h_kg,
    weight_delta_7d_kg,
    max_drop_1h_kg,
    -- temperature
    brood_temp_mean_c,
    brood_temp_std_c,
    brood_temp_mean_7d_c,
    temp_drift_7d_c,
    brood_temp_vs_peers_c,
    -- context
    rain_mm,
    temp_ext_mean_c,
    is_dry_day,
    battery_min_v,
    case
        when f_sensor_down then 'SENSOR_DOWN'
        when f_stale then 'STALE'
        when f_drift then 'SENSOR_DRIFT'
        when f_swarm or f_brood then 'ALERT'
        when f_sharp_drop or f_weight_loss or f_decline_7d or f_low_completeness then 'WATCH'
        else 'OK'
    end as status,
    -- flatten of one-element-or-empty lists: no lambda, works on every DuckDB version
    flatten([
        case when f_sensor_down      then ['SENSOR_DOWN']       else []::varchar[] end,
        case when f_stale            then ['STALE']             else []::varchar[] end,
        case when f_drift            then ['SENSOR_DRIFT']      else []::varchar[] end,
        case when f_swarm            then ['SWARM_SUSPECTED']   else []::varchar[] end,
        case when f_brood            then ['BROOD_TEMP']        else []::varchar[] end,
        case when f_sharp_drop and not f_swarm
                                     then ['SHARP_DROP']        else []::varchar[] end,
        case when f_weight_loss      then ['WEIGHT_LOSS']       else []::varchar[] end,
        case when f_decline_7d       then ['WEIGHT_DECLINE_7D'] else []::varchar[] end,
        case when f_low_completeness and not f_sensor_down
                                     then ['LOW_COMPLETENESS']  else []::varchar[] end
    ]) as status_reasons
from flags
