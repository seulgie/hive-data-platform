select beekeeper_id, date_local, priority_rank, count(*) as n
from {{ ref('fct_apiary_visit_priority_daily') }}
where priority_rank is not null
group by all
having count(*) > 1
