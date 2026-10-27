"""Run production event/timer callbacks across repeated and attribute-only reports."""
from __future__ import annotations

import ast
from datetime import timedelta
from decimal import Decimal
from runpy import run_path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_pv_integration_elapsed_time import SOURCE, START, _SensorHarness, _Trigger, _snapshot


# Load the real constant without importing Home Assistant's integration package.
DEFAULT_MIN_INTEGRATION_TIME = run_path(str(SOURCE.with_name("const.py")))[
    "DEFAULT_MIN_INTEGRATION_TIME"
]


class _EventSensor(_SensorHarness):
    """Supply a controllable HA clock and scheduler to the production callbacks."""

    def __init__(self, *, trigger=_Trigger.STATE_EVENT, start=0, source=None):
        self.now = START + timedelta(seconds=start)
        self.scheduled = []
        self.current_source = source or _snapshot("1", 0)
        self._source_entity_id = "sensor.pv_power"
        self._max_sub_interval = timedelta(seconds=30)
        self._cancel_max_sub_interval_exceeded_callback = None
        self.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _key: self.current_source))
        self.name = self.entity_id
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "SigenergyIntegrationSensor")
        names = {"_integrate_on_state_change", "_integrate_on_state_change_callback",
                 "_integrate_on_state_change_with_max_sub_interval",
                 "_schedule_max_sub_interval_exceeded_if_state_is_numeric"}
        functions = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
        namespace = {
            "Decimal": Decimal, "IntegrationTrigger": _Trigger,
            "STATE_UNKNOWN": "unknown", "STATE_UNAVAILABLE": "unavailable",
            "timedelta": timedelta, "DEFAULT_MIN_INTEGRATION_TIME": DEFAULT_MIN_INTEGRATION_TIME,
            "dt_util": SimpleNamespace(utcnow=lambda: self.now),
            "safe_decimal": lambda value: Decimal(str(value)),
            "async_call_later": self.schedule, "callback": lambda fn: fn, "_LOGGER": Mock(),
        }
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *functions,
        ], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
        super().__init__(namespace["_integrate_on_state_change"], trigger, self.now)
        for name in names:
            setattr(self, name, namespace[name].__get__(self))

    def _power_state_in_kw(self, state):
        return Decimal(state.state)

    def schedule(self, _hass, delay, callback):
        row = SimpleNamespace(at=self.now + delay, callback=callback, cancelled=False, fired=False)
        self.scheduled.append(row)
        return lambda: setattr(row, "cancelled", True)

    def fire_timer(self, seconds):
        self.now = START + timedelta(seconds=seconds)
        row = next(row for row in self.scheduled if not row.cancelled and not row.fired)
        assert row.at == self.now, "exercise the actual scheduled deadline"
        row.fired = True
        row.callback(self.now)

    def change(self, old, new, seconds, *, with_timer=True):
        self.now = START + timedelta(seconds=seconds)
        self.current_source = new
        event = SimpleNamespace(data={"old_state": old, "new_state": new})
        if with_timer:
            self._integrate_on_state_change_with_max_sub_interval(event)
        else:
            self._integrate_on_state_change_callback(event)


class PVIntegrationEventFlowTests(unittest.TestCase):
    def assert_area(self, sensor, kw_seconds):
        self.assertLess(abs(sensor._state - Decimal(str(kw_seconds)) / 3600), Decimal("1e-24"))

    def test_event_after_two_seconds_integrates_and_rearms_timer(self):
        sensor = _EventSensor()
        sensor._schedule_max_sub_interval_exceeded_if_state_is_numeric(sensor.current_source)
        sensor.change(_snapshot("1", 0, 1), _snapshot("1.2", 2), 2)
        self.assert_area(sensor, Decimal("2.2"))
        self.assertEqual(1, sensor.writes)
        self.assertEqual(START + timedelta(seconds=2), sensor._last_integration_time)
        self.assertTrue(sensor.scheduled[0].cancelled)
        sensor.fire_timer(32)
        self.assert_area(sensor, Decimal("38.2"))
        self.assertEqual(2, sensor.writes)

    def test_event_two_seconds_after_timer_counts_only_the_remaining_interval(self):
        sensor = _EventSensor(trigger=_Trigger.TIME_ELAPSED)
        sensor._schedule_max_sub_interval_exceeded_if_state_is_numeric(sensor.current_source)
        sensor.fire_timer(30)
        sensor.change(_snapshot("1", 0, 31), _snapshot("1.2", 32), 32)
        self.assert_area(sensor, Decimal("32.2"))
        self.assertEqual(2, sensor.writes)
        sensor.fire_timer(62)
        self.assert_area(sensor, Decimal("68.2"))
        self.assertEqual(3, sensor.writes)

    def test_attribute_event_then_timer_counts_the_entire_interval(self):
        sensor = _EventSensor()
        sensor._schedule_max_sub_interval_exceeded_if_state_is_numeric(sensor.current_source)
        sensor.change(_snapshot("1", 0, 9), _snapshot("1", 0, 10, updated_seconds=10), 10)
        sensor.fire_timer(40)
        self.assert_area(sensor, 40)
        self.assertEqual(2, sensor.writes)

    def test_timer_attribute_event_then_power_change_does_not_double_count(self):
        sensor = _EventSensor(trigger=_Trigger.TIME_ELAPSED)
        sensor._schedule_max_sub_interval_exceeded_if_state_is_numeric(sensor.current_source)
        sensor.fire_timer(30)
        sensor.change(_snapshot("1", 0, 39), _snapshot("1", 0, 40, updated_seconds=40), 40)
        sensor.change(_snapshot("1", 0, 49, updated_seconds=40), _snapshot("1.2", 50), 50)
        self.assert_area(sensor, 51)  # 40 s at 1 kW + 10 s averaging 1.1 kW
        self.assertEqual(3, sensor.writes)

    def test_repeated_reports_with_multiple_timer_checkpoints_are_not_recounted(self):
        sensor = _EventSensor(trigger=_Trigger.TIME_ELAPSED)
        sensor._schedule_max_sub_interval_exceeded_if_state_is_numeric(sensor.current_source)
        for at in (30, 60, 90):
            sensor.fire_timer(at)
        sensor.change(_snapshot("1", 0, 99), _snapshot("1.2", 100), 100)
        self.assert_area(sensor, 101)

    def test_first_event_after_restore_excludes_pre_restore_time(self):
        sensor = _EventSensor(trigger=_Trigger.TIME_ELAPSED, start=20)
        sensor.change(_snapshot("1", -100, 24), _snapshot("1.2", 25), 25)
        self.assert_area(sensor, Decimal(5) * Decimal("1.1"))

    def test_event_handler_without_timer_keeps_attribute_and_power_intervals(self):
        sensor = _EventSensor()
        sensor.change(_snapshot("1", 0, 9), _snapshot("1", 0, 10, updated_seconds=10), 10,
                      with_timer=False)
        sensor.change(_snapshot("1", 0, 19, updated_seconds=10), _snapshot("1.2", 20), 20,
                      with_timer=False)
        self.assert_area(sensor, 21)


if __name__ == "__main__":
    unittest.main()
