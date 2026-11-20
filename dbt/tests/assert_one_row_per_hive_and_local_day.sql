select hive_id, date_local, count(*) as n
from {{ ref('int_hive__daily') }}
group by hive_id, date_local
having count(*) > 1
