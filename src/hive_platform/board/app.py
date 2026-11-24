"""Field Ops board: what should each beekeeper do today?

Run from the repo root:   streamlit run src/hive_platform/board/app.py
Another warehouse:        HIVE_WAREHOUSE=path/to/warehouse.duckdb streamlit run ...
"""
from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

from hive_platform.board import queries as q

st.set_page_config(page_title="Hive Field Ops", page_icon="🐝", layout="wide")

NEED_COLORS = {                      # RGB per apiary need, most urgent first
    "ALERT": [214, 39, 40],
    "SENSOR": [148, 103, 189],
    "WATCH": [255, 158, 74],
    "OK": [44, 160, 44],
}
NEED_LEGEND = "🔴 alert: visit today (swarm, brood) · 🟣 sensor down, stale or drifting · " \
              "🟠 watch · 🟢 nothing to do. Bigger dot = higher priority score."
STATUS_ORDER = ["SENSOR_DOWN", "STALE", "SENSOR_DRIFT", "ALERT", "WATCH", "OK"]


def trend(df: pd.DataFrame, column: str, title: str) -> alt.Chart:
    """Line chart whose y axis fits the data (a temperature axis from 0 hides every change)."""
    return alt.Chart(df.reset_index()).mark_line(point=True).encode(
        x=alt.X("date_local:T", title=None),
        y=alt.Y(f"{column}:Q", title=title, scale=alt.Scale(zero=False)),
        tooltip=["date_local:T", alt.Tooltip(f"{column}:Q", format=".2f"), "status:N"],
    ).properties(height=220)


@st.cache_data(ttl=60)
def cached(name: str, *args):
    """Cache query results, not connections: the warehouse stays free for dbt build."""
    return getattr(q, name)(*args)


# --- sidebar --------------------------------------------------------------------------

lo, hi = cached("season_bounds")
with st.sidebar:
    st.header("Filters")
    demo_day = min(max(date(2025, 6, 4), lo), hi)   # a day with a swarm, inside the season
    day: date = st.date_input("Day", value=demo_day, min_value=lo, max_value=hi)
    beekeeper = st.selectbox("Beekeeper", ["All"] + cached("beekeepers"))
    beekeeper = None if beekeeper == "All" else beekeeper
    st.caption("Data: synthetic hives on real orchards (RPG 2024) and real weather "
               "(Open-Meteo). Statuses come from dbt marts.")

st.title("Hive Field Ops")
st.caption(f"Status on {day:%A %d %B %Y}" + (f" · {beekeeper}" if beekeeper else ""))

# --- KPI row --------------------------------------------------------------------------

counts = cached("status_counts", day, beekeeper).set_index("status")["hives"]
cols = st.columns(len(STATUS_ORDER))
for col, status in zip(cols, STATUS_ORDER):
    col.metric(status.replace("_", " ").title(), int(counts.get(status, 0)))

# --- map + visit list -----------------------------------------------------------------

apiaries = cached("apiary_map", day, beekeeper)
left, right = st.columns([3, 2])

with left:
    st.subheader("Apiaries")
    points = apiaries.assign(
        color=apiaries["need"].map(NEED_COLORS),
        radius=300 + 120 * apiaries["priority_score"],
    )
    st.pydeck_chart(pdk.Deck(
        initial_view_state=pdk.ViewState(
            latitude=float(points["lat"].mean()), longitude=float(points["lon"].mean()), zoom=9.2),
        layers=[pdk.Layer(
            "ScatterplotLayer", data=points, get_position=["lon", "lat"],
            get_fill_color="color", get_radius="radius", pickable=True,
            opacity=0.85, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1,
        )],
        tooltip={"text": "{apiary_id} · {commune_name}\n{beekeeper_id} · {crop_variety}\n"
                         "{need} · score {priority_score}\n{reasons}"},
    ))
    st.caption(NEED_LEGEND)

with right:
    st.subheader("Visit list")
    visits = cached("visit_list", day, beekeeper)
    if visits.empty:
        st.success("No apiary needs a visit today.")
    else:
        st.dataframe(visits, hide_index=True, width="stretch")

# --- drill-down -----------------------------------------------------------------------

st.subheader("Apiary detail")
order = apiaries.sort_values(["priority_score", "apiary_id"], ascending=[False, True])
apiary_id = st.selectbox(
    "Apiary", order["apiary_id"],
    format_func=lambda a: f"{a} · {order.set_index('apiary_id').loc[a, 'commune_name']} "
                          f"({order.set_index('apiary_id').loc[a, 'need']})")
hives = cached("apiary_hives", day, apiary_id)
st.dataframe(hives, hide_index=True, width="stretch")

hive_id = st.selectbox("Hive history (21 days)", hives["hive_id"])
history = cached("hive_history", hive_id, day).set_index("date_local")
c1, c2 = st.columns(2)
c1.altair_chart(trend(history, "weight_end_kg", "weight at day end (kg)"), width="stretch")
c2.altair_chart(trend(history, "brood_temp_mean_c", "brood temperature (°C)"), width="stretch")
st.caption("Rain (mm) on the same days: " +
           ", ".join(f"{d:%d/%m} {r:.0f}" for d, r in history["rain_mm"].items() if pd.notna(r)))

# --- rule quality ---------------------------------------------------------------------

with st.expander("How good are the alert rules? (scored against injected faults)"):
    st.dataframe(cached("rule_evaluation"), hide_index=True, width="stretch")
    st.caption("Possible only because faults are synthetic: each rule is scored on recall, "
               "precision, detection delay and false alerts a beekeeper would see.")
