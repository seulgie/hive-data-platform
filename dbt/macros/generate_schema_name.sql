{#- Use the folder's schema as is (staging, intermediate, marts) instead of dbt's default
    "<target schema>_<custom schema>" (main_staging). One warehouse, readable names. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}
