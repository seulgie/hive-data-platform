"""The pollination season, defined in local time and converted to UTC for storage.

Intervals are half-open [start, end): the season ends at the end of Oct 31, local time.
"""
from __future__ import annotations

import pandas as pd

LOCAL_TZ = "Europe/Paris"
SEASON_START_LOCAL = "2025-03-01"
SEASON_END_LOCAL = "2025-11-01"   # exclusive


def season_utc(start_local: str = SEASON_START_LOCAL,
               end_local: str = SEASON_END_LOCAL) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(start_local, tz=LOCAL_TZ).tz_convert("UTC")
    end = pd.Timestamp(end_local, tz=LOCAL_TZ).tz_convert("UTC")
    return start, end
