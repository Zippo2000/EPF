"""TC-S01 / TC-S02: the settings web page.

Uses Flask' test client, so no network and no live Immich are involved. These
guard two regressions:

* TC-S01 - a bare GET /setting must render (200). Historically the template
  dereferences battery_voltage / battery_percentage with a float format spec;
  if a code path omitted them from the Jinja context, Jinja raised
  UndefinedError and the page 500'd.
* TC-S02 - a POST with an invalid rotation must render a controlled error
  (200 + message), not a server error.
"""
import app as epf


def _client():
    return epf.app.test_client()


def test_settings_get_renders():
    resp = _client().get("/setting")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower()


def test_settings_invalid_rotation_renders_error_not_500():
    resp = _client().post("/setting", data={"rotation": "45"})
    # The validation error page is a normal 200 render, not an HTTP error.
    assert resp.status_code == 200
    assert b"Rotation must be" in resp.data
