"""TC-SL01 / TC-SL02: the /sleep wake-up scheduler endpoint.

Structural / contract checks that hold regardless of the wall-clock time the
test happens to run: the endpoint must return 200 JSON with the three
documented keys and an integral, non-negative millisecond sleep duration.
(The precise wake minute depends on the current time, so those assertions live
in the manual/live tier of the spec, not here.)
"""
import app as epf


def test_sleep_returns_well_formed_json():
    resp = epf.app.test_client().get("/sleep")
    assert resp.status_code == 200

    body = resp.get_json()
    assert isinstance(body, dict)

    for key in ("current_time", "next_wakeup", "sleep_duration"):
        assert key in body, f"missing key: {key}"

    # sleep_duration is an integer number of milliseconds and must not be
    # negative (a negative value would mean "wake in the past").
    assert isinstance(body["sleep_duration"], int)
    assert body["sleep_duration"] >= 0
    # Timestamps are fixed-format strings.
    assert len(body["current_time"]) == 19   # "YYYY-MM-DD HH:MM:SS"
    assert len(body["next_wakeup"]) == 19
