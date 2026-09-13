"""TC-U05: battery voltage -> percentage mapping.

Pure function, no network, no I/O. Verifies clamping at both ends and that the
mapping is monotonic non-decreasing across the usable voltage range.
"""
import app as epf

CALC = epf.calculate_battery_percentage


def test_clamp_at_full_charge():
    assert CALC(4200) == 100
    assert CALC(4500) == 100


def test_clamp_at_empty():
    assert CALC(3400) == 0
    assert CALC(3000) == 0


def test_monotonic_non_decreasing():
    # Walk the usable range in small steps; percentage must never decrease as
    # the voltage rises.
    prev = None
    for mv in range(3400, 4201, 5):
        pct = CALC(mv)
        assert 0 <= pct <= 100
        if prev is not None:
            assert pct >= prev, f"non-monotonic at {mv}mV: {pct} < {prev}"
        prev = pct
