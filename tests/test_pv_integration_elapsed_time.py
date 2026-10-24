"""Regression coverage for PV integration when source power reports repeat unchanged.

This test executes the real ``SigenergyIntegrationSensor._integrate_on_state_change``
method, extracted from its AST, without requiring the Home Assistant runtime.
Only its HA-facing collaborators are supplied by the small harness below.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import copy
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "calculated_sensor.py"
)
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Trigger(Enum):
    STATE_EVENT = "state_event"
    TIME_ELAPSED = "time_elapsed"


@dataclass(frozen=True)
class _State:
    state: str
    last_changed: datetime
    last_reported: datetime


def _get_integration_method():
    """Load only the production method; do not copy its algorithm into tests."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    sensor = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SigenergyIntegrationSensor"
    )
    function = next(
        node for node in sensor.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_integrate_on_state_change"
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            copy.deepcopy(function),
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "Decimal": Decimal,
        "IntegrationTrigger": _Trigger,
        "STATE_UNKNOWN": "unknown",
        "STATE_UNAVAILABLE": "unavailable",
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)  # noqa: S102
    return namespace["_integrate_on_state_change"]


class _SensorHarness:
    """Minimal test double for Home Assistant-specific entity plumbing."""

    log_this_entity = False
    entity_id = "sensor.test_pv_string_accumulated_energy"

    def __init__(self, method, trigger=_Trigger.STATE_EVENT, last_time=START):
        self._state = Decimal(0)
        self._last_integration_trigger = trigger
        self._last_integration_time = last_time
        self._method = method
        self.writes = 0

    @staticmethod
    def _validate_states(old_state, new_state):
        try:
            return Decimal(old_state.state), Decimal(new_state.state)
        except (ValueError, ArithmeticError):
            return None

    @staticmethod
    def _calculate_trapezoidal(elapsed_seconds, old_power, new_power):
        return elapsed_seconds * (old_power + new_power) / Decimal(2)

    def _update_integral(self, area):
        self._state += area / Decimal(3600)  # kW-seconds to kWh

    def async_write_ha_state(self):
        self.writes += 1

    def integrate(self, old_state, new_state):
        self._method(self, old_state, new_state)


def _snapshot(value, changed_seconds, reported_seconds=None):
    if reported_seconds is None:
        reported_seconds = changed_seconds
    return _State(
        str(value),
        START + timedelta(seconds=changed_seconds),
        START + timedelta(seconds=reported_seconds),
    )


class PVIntegrationElapsedTimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.method = staticmethod(_get_integration_method())

    def test_unchanged_five_second_reports_do_not_reduce_elapsed_time(self):
        """Unchanged polls advance last_reported but not last_changed."""
        for elapsed_seconds in (5, 10, 15, 20):
            with self.subTest(elapsed_seconds=elapsed_seconds):
                previous = _snapshot("1.0", 0, max(0, elapsed_seconds - 5))
                current = _snapshot("1.2", elapsed_seconds)
                sensor = _SensorHarness(self.method)
                sensor.integrate(previous, current)
                expected = Decimal(elapsed_seconds) * Decimal("1.1") / Decimal(3600)
                self.assertEqual(sensor._state, expected)
                self.assertEqual(sensor.writes, 1)

    def test_recent_unchanged_report_can_be_just_before_change(self):
        sensor = _SensorHarness(self.method)
        sensor.integrate(_snapshot("1", 0, 9), _snapshot("1.0", 10))
        self.assertEqual(sensor._state, Decimal(10) / Decimal(3600))

    def test_multiple_state_changes_cover_the_full_combined_interval(self):
        sensor = _SensorHarness(self.method)
        sensor.integrate(_snapshot("1", 0, 5), _snapshot("1.0", 10))
        sensor.integrate(_snapshot("1.0", 10, 20), _snapshot("1.00", 25))
        self.assertLess(
            abs(sensor._state - Decimal(25) / Decimal(3600)), Decimal("1e-24")
        )
        self.assertEqual(sensor.writes, 2)

    def test_timer_trigger_uses_last_integration_checkpoint(self):
        """The state handler must not re-integrate time already covered by a timer."""
        sensor = _SensorHarness(
            self.method,
            trigger=_Trigger.TIME_ELAPSED,
            last_time=START + timedelta(seconds=8),
        )
        sensor.integrate(_snapshot("1.0", 0, 9), _snapshot("1.0", 10))
        self.assertEqual(sensor._state, Decimal(2) / Decimal(3600))
        self.assertEqual(sensor.writes, 1)

    def test_normal_state_changes_still_use_trapezoidal_integration(self):
        sensor = _SensorHarness(self.method)
        sensor.integrate(_snapshot("0.6", 0), _snapshot("1.4", 10))
        self.assertEqual(sensor._state, Decimal(10) / Decimal(3600))

    def test_missing_or_unavailable_states_are_not_integrated(self):
        cases = (
            (None, _snapshot("1", 10)),
            (_snapshot("1", 0), None),
            (_snapshot("unknown", 0), _snapshot("1", 10)),
            (_snapshot("unavailable", 0), _snapshot("1", 10)),
        )
        for old_state, new_state in cases:
            with self.subTest(old_state=old_state, new_state=new_state):
                sensor = _SensorHarness(self.method)
                sensor.integrate(old_state, new_state)
                self.assertEqual(sensor._state, Decimal(0))
                self.assertEqual(sensor.writes, 0)


if __name__ == "__main__":
    unittest.main()
