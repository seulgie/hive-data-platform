"""Where do the remaining BROOD_TEMP false alerts come from?

Breaks the alert days down by month and by condition, and compares each hive with the
other hives of its apiary on the same day. Usage:  python scripts/diagnose_brood_alerts.py
"""
import duckdb

QUERY = """
with s as (
    select
        *,
        brood_temp_mean_c
            - median(brood_temp_mean_c) over (partition by apiary_id, date_local) as vs_peers_c
    from marts.fct_hive_status_daily
),
alerts as (
    select * from s
    where status = 'ALERT' and list_contains(status_reasons, 'BROOD_TEMP')
)
select
    strftime(date_local, '%Y-%m')                              as month,
    count(*)                                                   as alert_days,
    count(*) filter (where brood_temp_mean_c < 33)             as low_mean,
    count(*) filter (where brood_temp_std_c > 1.5)             as high_std,
    round(avg(temp_ext_mean_c), 1)                             as avg_outside_c,
    round(avg(brood_temp_mean_c), 2)                           as avg_brood_c,
    round(avg(vs_peers_c), 2)                                  as avg_vs_peers_c,
    round(min(vs_peers_c), 2)                                  as min_vs_peers_c
from alerts
group by month
order by month
"""

PEERS = """
with s as (
    select
        brood_temp_mean_c
            - median(brood_temp_mean_c) over (partition by apiary_id, date_local) as vs_peers_c
    from marts.fct_hive_status_daily
    where status <> 'SENSOR_DRIFT'
)
select
    round(quantile_cont(vs_peers_c, 0.001), 2) as p0_1,
    round(quantile_cont(vs_peers_c, 0.01), 2)  as p1,
    round(min(vs_peers_c), 2)                  as min_c
from s
"""

con = duckdb.connect("data/lake/warehouse.duckdb", read_only=True)
print(con.sql(QUERY))
print(con.sql(PEERS))