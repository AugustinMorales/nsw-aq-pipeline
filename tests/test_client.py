import io
import json
import urllib.error
from datetime import date

import pytest

from nsw_pipeline import client


class FakeResponse:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def scripted_opener(*outcomes):
    """An opener that returns or raises each outcome in turn, recording requests."""
    calls = []

    def opener(request, timeout):
        calls.append(request)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    opener.calls = calls
    return opener


def http_error(code, body=""):
    return urllib.error.HTTPError("https://example", code, "error", {}, io.BytesIO(body.encode()))


def no_sleep(seconds):
    no_sleep.waits.append(seconds)


@pytest.fixture(autouse=True)
def reset_sleep():
    no_sleep.waits = []


def test_success_first_try():
    opener = scripted_opener('[{"a": 1}]')
    data, text = client.fetch_json("get_SiteDetails", opener=opener, sleep=no_sleep)
    assert data == [{"a": 1}]
    assert text == '[{"a": 1}]'
    assert no_sleep.waits == []


def test_retries_transient_errors_with_backoff():
    opener = scripted_opener(
        urllib.error.URLError("connection reset"),
        http_error(503),
        TimeoutError("timed out"),
        "[]",
    )
    data, _ = client.fetch_json("get_SiteDetails", opener=opener, sleep=no_sleep,
                                retries=4, backoff_s=10)
    assert data == []
    assert no_sleep.waits == [10, 20, 40]


def test_retries_unreadable_json():
    opener = scripted_opener("<html>maintenance</html>", "[]")
    data, _ = client.fetch_json("get_SiteDetails", opener=opener, sleep=no_sleep)
    assert data == []


def test_gives_up_after_all_retries():
    opener = scripted_opener(*[http_error(502)] * 3)
    with pytest.raises(client.FetchError, match="after 3 attempts"):
        client.fetch_json("get_SiteDetails", opener=opener, sleep=no_sleep, retries=3)
    assert len(opener.calls) == 3


def test_bad_request_fails_immediately():
    opener = scripted_opener(http_error(400, "Invalid parameter combination"))
    with pytest.raises(client.FetchError, match="Invalid parameter combination"):
        client.fetch_json("get_Observations", {}, opener=opener, sleep=no_sleep)
    assert len(opener.calls) == 1


def test_observations_request_body():
    body = client.observations_body([39, 107], date(2026, 10, 31), date(2026, 11, 2))
    assert body == {
        "Parameters": ["CO", "NO2", "OZONE", "PM10", "PM2.5", "SO2"],
        "Sites": [39, 107],
        "StartDate": "2026-10-31",
        "EndDate": "2026-11-02",
        "Categories": ["Averages"],
        "SubCategories": ["Hourly"],
        "Frequency": ["Hourly average"],
    }


def test_observations_are_split_by_pollutant_and_site_batch():
    opener = scripted_opener(*['[{"x": 1}]'] * 12)
    records, text, failures = client.fetch_observations(
        list(range(1, 51)), date(2026, 10, 31), date(2026, 11, 1),
        sites_per_request=30, opener=opener, sleep=no_sleep)
    # 6 pollutants x 2 site batches (30 + 20)
    assert len(opener.calls) == 12
    bodies = [json.loads(c.data) for c in opener.calls]
    assert all(c.get_method() == "POST" and c.full_url.endswith("/get_Observations") for c in opener.calls)
    assert [b["Parameters"] for b in bodies[:2]] == [["CO"], ["CO"]]
    assert [len(b["Sites"]) for b in bodies[:2]] == [30, 20]
    assert {b["Parameters"][0] for b in bodies} == {"CO", "NO2", "OZONE", "PM10", "PM2.5", "SO2"}
    assert len(records) == 12 and failures == []
    assert json.loads(text) == records


def test_one_failed_piece_does_not_lose_the_rest():
    outcomes = ['[{"x": 1}]'] * 6
    outcomes[2] = http_error(400, "bad combination")
    opener = scripted_opener(*outcomes)
    records, _, failures = client.fetch_observations(
        [39], date(2026, 10, 31), date(2026, 11, 1), opener=opener, sleep=no_sleep)
    assert len(records) == 5
    # Pieces go in alphabetical order (CO, NO2, OZONE, ...), so the third is OZONE
    assert len(failures) == 1 and "OZONE" in failures[0] and "bad combination" in failures[0]


def test_all_pieces_failing_is_an_error():
    opener = scripted_opener(*[http_error(400)] * 6)
    with pytest.raises(client.FetchError, match="all 6 observation requests failed"):
        client.fetch_observations([39], date(2026, 10, 31), date(2026, 11, 1),
                                  opener=opener, sleep=no_sleep)


def test_time_budget_skips_remaining_pieces():
    ticks = iter([0, 0, 0, 0, 0, 1000, 1000, 1000, 1000, 1000, 1000, 1000])
    opener = scripted_opener(*['[{"x": 1}]'] * 6)
    records, _, failures = client.fetch_observations(
        [39], date(2026, 10, 31), date(2026, 11, 1), time_budget_s=900,
        clock=lambda: next(ticks), opener=opener, sleep=no_sleep)
    assert len(opener.calls) == 2
    assert len(records) == 2
    assert len(failures) == 4 and all("time budget" in f for f in failures)


def test_only_sites_that_measure_each_pollutant_are_requested():
    opener = scripted_opener(*['[]'] * 3)
    client.fetch_observations(
        [1, 2, 3], date(2026, 10, 31), date(2026, 11, 1),
        sites_by_parameter={"CO": [1], "PM2.5": [2, 3]}, sites_per_request=1,
        opener=opener, sleep=no_sleep)
    bodies = [json.loads(c.data) for c in opener.calls]
    assert [(b["Parameters"], b["Sites"]) for b in bodies] == [
        (["CO"], [1]), (["PM2.5"], [2]), (["PM2.5"], [3])]


def test_latest_hour_uses_empty_body():
    opener = scripted_opener('[{"Site_Id": 39}]')
    records, _ = client.fetch_latest(opener=opener, sleep=no_sleep)
    assert records == [{"Site_Id": 39}]
    assert opener.calls[0].get_method() == "POST"
    assert opener.calls[0].data == b'""'


def test_non_list_response_is_an_error():
    opener = scripted_opener('{"message": "unexpected"}')
    with pytest.raises(client.FetchError, match="did not return a list"):
        client.fetch_sites(opener=opener, sleep=no_sleep)
