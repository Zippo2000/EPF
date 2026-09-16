"""TC-SL03 / TC-SL04 / TC-N07: battery display on the settings page.

All tests monkeypatch the two module-level globals that /download writes
(last_battery_voltage, last_battery_update), then assert what /setting renders.
No network or Immich involved.
"""
import time

import app as epf


# ── TC-SL03: fresh reading ───────────────────────────────────────────────

def test_fresh_reading(monkeypatch):
    """A reading < 1 h old is displayed with the correct percentage."""
    monkeypatch.setattr(epf, "last_battery_voltage", 3950)
    monkeypatch.setattr(epf, "last_battery_update", time.time())
    html = epf.app.test_client().get("/setting").get_data(as_text=True)
    pct = epf.calculate_battery_percentage(3950)  # derive, don't hard-code
    assert f"Charge Level: {pct:.1f}%" in html


def test_zero_voltage(monkeypatch):
    """Explicit zero voltage → 0.0 % (not an error, not a crash)."""
    monkeypatch.setattr(epf, "last_battery_voltage", 0)
    monkeypatch.setattr(epf, "last_battery_update", time.time())
    assert "Charge Level: 0.0%" in epf.app.test_client().get("/setting").get_data(as_text=True)


# ── TC-SL04: stale reading ──────────────────────────────────────────────

def test_stale_reading_forced_zero(monkeypatch):
    """A reading 2 h old is treated as 'no data' → forced 0 %."""
    monkeypatch.setattr(epf, "last_battery_voltage", 3950)
    monkeypatch.setattr(epf, "last_battery_update", time.time() - 7200)  # 2 h old
    html = epf.app.test_client().get("/setting").get_data(as_text=True)
    assert "Charge Level: 0.0%" in html  # stale ⇒ "no data", NOT the 3950-derived value


# ── TC-N07: NaN / invalid value ─────────────────────────────────────────

def test_nan_voltage_shows_zero(monkeypatch):
    """If last_battery_voltage is NaN (e.g. from a corrupted write), /setting still renders 0 %."""
    monkeypatch.setattr(epf, "last_battery_voltage", float("nan"))
    monkeypatch.setattr(epf, "last_battery_update", time.time())
    html = epf.app.test_client().get("/setting").get_data(as_text=True)
    assert "Charge Level: 0.0%" in html
