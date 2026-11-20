{#- Local-time helpers. Timestamps are stored in UTC; the business works in local days.
    DuckDB's timezone(tz, x) goes both ways:
      timestamptz -> local wall-clock timestamp
      timestamp (wall clock in tz) -> timestamptz -#}

{% macro to_local_date(ts_utc) -%}
    cast(timezone('{{ var("local_tz") }}', {{ ts_utc }}) as date)
{%- endmacro %}

{% macro local_midnight_utc(local_date) -%}
    timezone('{{ var("local_tz") }}', cast({{ local_date }} as timestamp))
{%- endmacro %}
