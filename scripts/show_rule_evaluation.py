"""Print the rule evaluation and today's visit list. Usage: python scripts/show_rule_evaluation.py"""
import duckdb

con = duckdb.connect("data/lake/warehouse.duckdb", read_only=True)
print(con.sql("select * from evaluation.eval_alert_rules"))
print(con.sql("""
    select status, count(*) as hive_days
    from marts.fct_hive_status_daily group by status order by hive_days desc
"""))