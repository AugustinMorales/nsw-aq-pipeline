"""Shared test data: real API responses saved on 2026-10-31 at 22:08 UTC."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_FETCHED_AT = datetime(2026, 10, 31, 22, 8, 15, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def sites():
    return load("sites.json")


@pytest.fixture
def observations():
    return load("observations_current.json")


def make_record(site_id=39, code="PM2.5", units="µg/m³", value=7.2,
                date="2026-11-01", hour=7, category="Averages",
                sub_category="Hourly", frequency="Hourly average"):
    """A single observation shaped like the API's."""
    return {
        "Site_Id": site_id,
        "Parameter": {
            "ParameterCode": code, "ParameterDescription": code,
            "Units": units, "UnitsDescription": units,
            "Category": category, "SubCategory": sub_category, "Frequency": frequency,
        },
        "Date": date, "Hour": hour, "HourDescription": "",
        "Value": value, "AirQualityCategory": None, "DeterminingPollutant": None,
    }
