"""How do the injected swarms (ground truth) look in the daily table?

Reads the warehouse only. Usage (from the repo root):  python scripts/check_swarm_detection.py
"""
import duckdb

QUERY = """
select
    count(*)                                              as swarms,
    round(min(d.max_drop_1h_kg), 2)                       as min_seen_kg,
    round(min(f.magnitude), 2)                            as min_injected_kg,
    sum(case when d.max_drop_1h_kg > 1.5 then 1 else 0 end) as over_1_5kg,
    sum(case when d.max_drop_1h_kg > 1.0 then 1 else 0 end) as over_1_0kg
from intermediate.int_hive__daily as d
join staging.stg_iot__fault_log as f
  on f.hive_id = d.hive_id
where f.fault_type = 'SWARM'
  and d.date_local = cast(timezone('Europe/Paris', f.start_utc) as date)
"""

HEALTHY = """
select
    round(quantile_cont(max_drop_1h_kg, 0.99), 2) as healthy_p99_kg,
    round(max(max_drop_1h_kg), 2)                 as healthy_max_kg
from intermediate.int_hive__daily
where hive_id not in (
    select hive_id from staging.stg_iot__fault_log where fault_type = 'SWARM'
)
"""

con = duckdb.connect("data/lake/warehouse.duckdb", read_only=True)
con.sql("set TimeZone = 'UTC'")
print(con.sql(QUERY))
print(con.sql(HEALTHY))