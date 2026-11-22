-- Lineage guard: the injected ground truth (stg_iot__fault_log) may be read by
-- models in models/evaluation/ only. Any other model reading it would leak the answer
-- into the marts. Checked on the dbt graph itself, so it fails at build time.
{%- set offenders = [] -%}
{%- if execute -%}
  {%- for node in graph.nodes.values() -%}
    {%- if node.resource_type == 'model'
          and 'evaluation' not in node.fqn
          and 'model.hive_platform.stg_iot__fault_log' in node.depends_on.nodes -%}
      {%- do offenders.append(node.name) -%}
    {%- endif -%}
  {%- endfor -%}
{%- endif %}
select '{{ offenders | join(", ") }}' as models_reading_ground_truth
where {{ offenders | length }} > 0
