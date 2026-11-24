"""Field Ops board (Streamlit). Reads the dbt marts in the DuckDB warehouse, read-only.

    queries  SQL against the marts, one function per board element (testable without UI)
    app      Streamlit layout: date + beekeeper filters, map, visit list, hive drill-down

Run from the repo root:  streamlit run src/hive_platform/board/app.py
"""
