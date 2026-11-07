"""client.py: the request we send."""
from hive_platform.ingest.weather.client import fetch_archive

from .factories import FakeOpenMeteo


def test_fetch_archive_asks_for_utc_and_all_variables():
    session = FakeOpenMeteo()
    fetch_archive(session, 44.1, 1.1, "2025-02-28", "2025-10-31",
                  ("temperature_2m", "precipitation", "wind_speed_10m"))

    params = session.calls[0]
    assert params["timezone"] == "GMT"
    assert params["hourly"] == "temperature_2m,precipitation,wind_speed_10m"
    assert (params["start_date"], params["end_date"]) == ("2025-02-28", "2025-10-31")
