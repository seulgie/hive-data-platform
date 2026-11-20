-- The point-in-time join must neither drop nor duplicate readings.
select s.n as staging_rows, i.n as joined_rows
from (select count(*) as n from {{ ref('stg_telemetry__readings') }}) as s,
     (select count(*) as n from {{ ref('int_readings__with_hive') }}) as i
where s.n <> i.n
