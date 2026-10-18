"""PV restore selection and entity lifecycle with HA-compatible test doubles."""
from __future__ import annotations

import __future__
import ast
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import importlib.util
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"
spec = importlib.util.spec_from_file_location("sigen_pv_restore_test", ROOT / "pv_restore.py")
pv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pv
spec.loader.exec_module(pv)
UTC = timezone.utc
NOW = datetime(2026, 10, 17, 12, 15, tzinfo=UTC)


def state(value, at=NOW, unit="kWh"):
    return SimpleNamespace(state=str(value), attributes={"unit_of_measurement": unit},
                           last_updated=at, last_reported=at)


class RestoreSelectionTests(unittest.TestCase):
    def choose(self, snapshot, recorder, *, daily=False, now=NOW, had_snapshot=True):
        return pv.choose_restore(snapshot, pv.RecorderRestore(recorder, recorder is not None),
                                 had_snapshot, NOW, now, daily)

    def test_observed_older_snapshot_is_replaced_by_latest_recorder_value(self):
        snapshot = pv.snapshot_sample(None, state("12.471733", NOW - timedelta(minutes=7)))
        recorded = pv.sample_from_state(state("12.500856", NOW - timedelta(minutes=6)))
        self.assertEqual(Decimal("12.500856"), self.choose(snapshot, recorded, daily=True).value)

    def test_newest_sample_is_used_instead_of_largest_historical_value(self):
        snapshot = pv.snapshot_sample(None, state(25, NOW - timedelta(minutes=3)))
        recorded = pv.sample_from_state(state(5, NOW - timedelta(minutes=1)))
        self.assertEqual(Decimal(5), self.choose(snapshot, recorded).value)

    def test_newer_native_snapshot_and_equal_timestamp_keep_native_precision(self):
        for age in (timedelta(), timedelta(seconds=1)):
            with self.subTest(age=age):
                native = SimpleNamespace(native_value=Decimal("16.538346123"),
                                         native_unit_of_measurement="kWh")
                snapshot = pv.snapshot_sample(native, state("0.016538346", NOW, "MWh"))
                recorded = pv.sample_from_state(state("0.016538346", NOW - age, "MWh"))
                self.assertEqual(native.native_value, self.choose(snapshot, recorded).value)

    def test_historical_display_units_are_converted_independently(self):
        for value, unit in (("16538.346", "Wh"), ("16.538346", "kWh"),
                            ("0.016538346", "MWh")):
            with self.subTest(unit=unit):
                recorded = pv.sample_from_state(state(value, NOW - timedelta(seconds=1), unit))
                self.assertEqual(Decimal("16.538346"), self.choose(None, recorded).value)

    def test_invalid_values_and_units_never_become_zero(self):
        for value, unit in ((None, "kWh"), ("unknown", "kWh"), ("unavailable", "kWh"),
                            ("NaN", "kWh"), ("Infinity", "kWh"), (-1, "kWh"),
                            (5, None), (5, "bananas")):
            with self.subTest(value=value, unit=unit):
                self.assertIsNone(pv.energy_in_kwh(value, unit))

    def test_unknown_snapshot_timestamp_cannot_hide_newer_recorder_data(self):
        native = SimpleNamespace(native_value=5, native_unit_of_measurement="kWh")
        snapshot = pv.snapshot_sample(native, state("unavailable"))
        self.assertIsNone(snapshot)
        recorded = pv.sample_from_state(state(6, NOW - timedelta(minutes=1)))
        self.assertEqual(Decimal(6), self.choose(snapshot, recorded).value)

    def test_same_day_daily_counter_restores_but_previous_day_starts_new_period(self):
        local = NOW.astimezone(ZoneInfo("Europe/Stockholm"))
        for when, expected in ((NOW - timedelta(minutes=1), Decimal(12)),
                               (NOW - timedelta(days=1), Decimal(0))):
            with self.subTest(when=when):
                recorded = pv.sample_from_state(state(12, when))
                self.assertEqual(expected, self.choose(None, recorded, daily=True, now=local).value)
                self.assertEqual(Decimal(12), self.choose(None, recorded, now=local).value)

    def test_day_boundary_uses_ha_timezone_instead_of_utc_date(self):
        cutoff = datetime(2026, 10, 17, 22, 5, tzinfo=UTC)
        previous = pv.sample_from_state(state(12, cutoff - timedelta(minutes=10)))
        result = pv.choose_restore(None, pv.RecorderRestore(previous, True), False, cutoff,
                                   cutoff.astimezone(ZoneInfo("Europe/Stockholm")), True)
        self.assertEqual(Decimal(0), result.value)

    def test_existing_invalid_data_requires_retry_but_new_installation_can_start_zero(self):
        with self.assertRaises(ValueError):
            self.choose(None, None)
        with self.assertRaises(ValueError):
            pv.choose_restore(None, pv.RecorderRestore(None, True), False, NOW, NOW, False)
        self.assertEqual(Decimal(0), self.choose(None, None, had_snapshot=False).value)

    def test_future_snapshot_cannot_be_used_as_startup_baseline(self):
        future = pv.snapshot_sample(None, state(12, NOW + timedelta(seconds=1)))
        with self.assertRaises(ValueError):
            self.choose(future, None)

    def test_debug_decision_identifies_newer_recorder_and_normalized_candidates(self):
        snapshot = pv.snapshot_sample(None, state("12.471733", NOW - timedelta(minutes=7)))
        recorded = pv.sample_from_state(state("0.012500856", NOW - timedelta(minutes=6), "MWh"))
        with self.assertLogs(pv._LOGGER, level="DEBUG") as logs:
            result = pv.choose_restore(snapshot, pv.RecorderRestore(recorded, True),
                                       True, NOW, NOW, False, entity_id="sensor.pv_test")
        output = "\n".join(logs.output)
        self.assertEqual(Decimal("12.500856"), result.value)
        self.assertIn("sensor.pv_test", output)
        self.assertIn("12.471733", output)
        self.assertIn("12.500856", output)
        self.assertIn("values in kWh", output)
        self.assertIn("newest timestamp", output)
        self.assertTrue(all(r.levelno == logging.DEBUG for r in logs.records))

    def test_debug_tie_explains_native_precision(self):
        native = SimpleNamespace(native_value=Decimal("6333.170261123"), native_unit_of_measurement="kWh")
        snapshot = pv.snapshot_sample(native, state("6.333170261", NOW, "MWh"))
        recorded = pv.sample_from_state(state("6.333170261", NOW, "MWh"))
        with self.assertLogs(pv._LOGGER, level="DEBUG") as logs:
            result = self.choose(snapshot, recorded)
        self.assertEqual(native.native_value, result.value)
        self.assertIn("native snapshot precision at equal timestamp", "\n".join(logs.output))

    def test_debug_distinguishes_new_counter_new_day_and_invalid_existing_data(self):
        with self.assertLogs(pv._LOGGER, level="DEBUG") as logs:
            self.choose(None, None, had_snapshot=False)
            self.choose(None, pv.sample_from_state(state(12, NOW - timedelta(days=1))), daily=True)
            with self.assertRaises(ValueError):
                self.choose(pv.snapshot_sample(None, state(12, NOW + timedelta(seconds=1))), None)
        output = "\n".join(logs.output)
        for reason in ("new_sensor", "new_day", "previous local day", "timestamp after startup cutoff", "refusing zero"):
            self.assertIn(reason, output)


class RecorderQueryTests(unittest.TestCase):
    def setUp(self):
        self.hass = object()
        self.entity_id = "sensor.pv_energy"
        self.last, self.period = [], []
        self.period_calls = []
        module = ModuleType("homeassistant.components.recorder.history")

        def last_state(hass, count, entity_id):
            self.assertIs(self.hass, hass)
            self.assertEqual(1, count, "use HA's indexed single-state query")
            self.assertEqual(self.entity_id, entity_id)
            return {entity_id: self.last} if self.last else {}

        def period(hass, start, end, entity_id, *, no_attributes, include_start_time_state):
            self.assertIs(self.hass, hass)
            self.assertFalse(no_attributes, "history units must remain available")
            self.assertFalse(include_start_time_state, "avoid synthetic start timestamps")
            self.period_calls.append((start, end))
            return {entity_id: self.period}

        module.get_last_state_changes = last_state
        module.state_changes_during_period = period
        self.patch = patch.dict(sys.modules, {module.__name__: module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def read(self, snapshot=None):
        return pv.read_recorder_restore(self.hass, self.entity_id, NOW, snapshot)

    def test_numeric_latest_state_needs_only_one_indexed_query(self):
        self.last = [state(12, NOW - timedelta(seconds=1))]
        self.assertEqual(Decimal(12), self.read().sample.value)
        self.assertEqual([], self.period_calls)

    def test_trailing_unavailable_finds_latest_valid_state_before_fixed_cutoff(self):
        snapshot = pv.snapshot_sample(None, state(10, NOW - timedelta(minutes=3)))
        self.last = [state("unavailable", NOW - timedelta(seconds=1))]
        self.period = [state(11, NOW - timedelta(minutes=2)),
                       state(12, NOW - timedelta(minutes=1)), self.last[0]]
        self.assertEqual(Decimal(12), self.read(snapshot).sample.value)
        self.assertEqual(NOW, self.period_calls[0][1])
        self.assertLess(self.period_calls[0][0], snapshot.updated)

    def test_post_start_unknown_state_is_not_evidence_of_old_counter(self):
        self.last = [state("unknown", NOW + timedelta(seconds=1))]
        result = self.read()
        self.assertIsNone(result.sample)
        self.assertFalse(result.has_history)

    def test_old_unavailable_record_is_not_misclassified_as_new_installation(self):
        self.last = [state("unavailable", NOW - timedelta(seconds=1))]
        self.assertTrue(self.read().has_history)

    def test_queue_wait_and_query_time_are_measured_independently(self):
        self.last = [state(12, NOW - timedelta(seconds=1))]
        with patch.object(pv, "monotonic", side_effect=[15, 15.25]):
            result = pv.read_recorder_restore(self.hass, self.entity_id, NOW, None, 10)
        self.assertEqual(5, result.queue_wait)
        self.assertEqual(.25, result.query_time)
        self.assertEqual(Decimal(12), result.sample.value)
        self.assertEqual([], self.period_calls)

    def test_query_failure_logs_timings_without_masking_the_error(self):
        with patch.object(pv, "monotonic", side_effect=[12, 12.5]), \
             patch.object(pv, "_read_recorder_restore", side_effect=OSError("database not ready")), \
             self.assertLogs(pv._LOGGER, level="DEBUG") as logs:
            with self.assertRaisesRegex(OSError, "database not ready"):
                pv.read_recorder_restore(self.hass, self.entity_id, NOW, None, 10)
        output = "\n".join(logs.output)
        self.assertIn(self.entity_id, output)
        self.assertIn("queue_wait=2 s", output)
        self.assertIn("query_time=0.500 s", output)

    def test_fallback_query_debug_identifies_invalid_latest_state(self):
        self.last = [state("unavailable", NOW - timedelta(seconds=1))]
        self.period = [state(12, NOW - timedelta(minutes=1))]
        with self.assertLogs(pv._LOGGER, level="DEBUG") as logs:
            result = self.read()
        self.assertEqual(Decimal(12), result.sample.value)
        self.assertIn("searching bounded pre-start history", "\n".join(logs.output))


class Cancel:
    def __init__(self):
        self.cancelled = False

    def __call__(self):
        self.cancelled = True


class FakeEntity:
    def __init__(self, coordinator, description, **kwargs):
        self.fixture = coordinator.fixture
        self.coordinator = coordinator
        self.hass, self.hub = coordinator.hass, coordinator.hub
        self.entity_id = "sensor.pv_energy"
        self.registry_entry = SimpleNamespace(created_at=self.fixture.registry_created)
        self.entity_description = description
        self.name = "PV Energy"

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass

    def async_on_remove(self, callback):
        self.fixture.on_remove.append(callback)

    def async_write_ha_state(self):
        self.fixture.writes.append(self.native_value)


class FakeRestoreSensor:
    def __init__(self):
        pass

    async def async_get_last_sensor_data(self):
        return self.fixture.native

    async def async_get_last_state(self):
        return self.fixture.saved


class EntityRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = NOW
        self.clock = 100.0
        self.native = None
        self.saved = state("12.471733", NOW - timedelta(minutes=7))
        self.recorded = pv.RecorderRestore(
            pv.sample_from_state(state("12.500856", NOW - timedelta(minutes=6))), True)
        self.executor_calls, self.on_remove, self.writes = [], [], []
        self.timers, self.points, self.listeners, self.tasks = [], [], [], []
        self.pending_future = None
        self.worker_error = None
        self.registry_created = NOW - timedelta(days=1)
        self.config_updates = []
        self.source = state(2, NOW - timedelta(minutes=30), "kW")
        self.hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda entity_id: self.source),
            config_entries=SimpleNamespace(async_update_entry=lambda *args, **kwargs:
                                           self.config_updates.append(kwargs)))
        entry = SimpleNamespace(data={}, async_create_task=self.create_task)
        self.coordinator = SimpleNamespace(hass=self.hass, hub=SimpleNamespace(config_entry=entry), fixture=self)
        self.namespace = {
            "asyncio": asyncio, "Decimal": Decimal, "InvalidOperation": InvalidOperation,
            "datetime": datetime, "timedelta": timedelta, "_LOGGER": logging.getLogger(__name__),
            "monotonic": lambda: self.clock,
            "SigenergyEntity": FakeEntity, "RestoreSensor": FakeRestoreSensor,
            "callback": lambda fn: fn, "safe_decimal": lambda v: Decimal(str(v)) if v is not None else Decimal(0),
            "STATE_UNKNOWN": "unknown", "STATE_UNAVAILABLE": "unavailable",
            "ATTR_UNIT_OF_MEASUREMENT": "unit_of_measurement", "CONF_VALUES_TO_INIT": "values_to_init",
            "LOG_THIS_ENTITY": [], "_POWER_TO_KILO_WATT": {"kW": Decimal(1)},
            "_ENERGY_TO_KILO_WATT_HOUR": pv.ENERGY_TO_KWH,
            "_PV_INTEGRATOR_KEYS": {"pv_string_daily_energy", "pv_string_accumulated_energy"},
            "_PV_RESTORE_TIMEOUT": 0, "_PV_RESTORE_RETRY": 60,
            "choose_restore": pv.choose_restore, "snapshot_sample": pv.snapshot_sample,
            "energy_in_kwh": pv.energy_in_kwh, "read_recorder_restore": object(),
            "get_instance": lambda hass: SimpleNamespace(async_add_executor_job=self.executor),
            "dt_util": SimpleNamespace(now=lambda: self.now, utcnow=lambda: self.now.astimezone(UTC)),
            "IntegrationTrigger": SimpleNamespace(STATE_EVENT="state", TIME_ELAPSED="time"),
            "async_call_later": self.call_later, "async_track_point_in_time": self.point_in_time,
            "async_track_state_change_event": self.track_states,
        }
        tree = ast.parse((ROOT / "calculated_sensor.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "SigenergyIntegrationSensor")
        exec(compile(ast.Module(body=[cls], type_ignores=[]), "calculated_sensor.py", "exec",
                     flags=__future__.annotations.compiler_flag), self.namespace)
        self.cls = self.namespace["SigenergyIntegrationSensor"]

    def entity(self, daily=False):
        description = SimpleNamespace(
            key="pv_string_daily_energy" if daily else "pv_string_accumulated_energy",
            state_class="total_increasing" if daily else "total", native_unit_of_measurement="kWh",
            round_digits=6, max_sub_interval=timedelta(seconds=30))
        return self.cls(self.coordinator, description, "PV", "pv_string", source_entity_id="sensor.pv_power")

    def executor(self, function, *args):
        self.executor_calls.append(args)
        if self.pending_future is not None:
            return self.pending_future
        future = asyncio.get_running_loop().create_future()
        if self.worker_error:
            future.set_exception(self.worker_error)
        else:
            future.set_result(self.recorded)
        return future

    def call_later(self, hass, delay, callback):
        cancel = Cancel()
        self.timers.append(SimpleNamespace(delay=delay, callback=callback, cancel=cancel))
        return cancel

    def point_in_time(self, hass, callback, when):
        self.points.append(SimpleNamespace(callback=callback, when=when))
        return Cancel()

    def track_states(self, hass, ids, callback):
        self.listeners.append(callback)
        return Cancel()

    def create_task(self, hass, coro, **kwargs):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def test_entity_restores_latest_value_before_starting_integration(self):
        entity = self.entity()
        await entity.async_added_to_hass()
        self.assertEqual(Decimal("12.500856"), entity.native_value)
        self.assertEqual(1, len(self.executor_calls))
        self.assertEqual(1, len(self.listeners))
        self.assertFalse(entity._pv_restore_pending)
        self.assertEqual(self.now, entity._last_integration_time)

    async def test_info_logs_restore_source_value_unit_and_timings_once(self):
        self.recorded = pv.RecorderRestore(self.recorded.sample, True, .25, .125)
        entity = self.entity()
        with self.assertLogs(self.namespace["_LOGGER"], level="INFO") as logs:
            await entity.async_added_to_hass()
            self.now += timedelta(seconds=5)
            entity._integrate_on_state_change(state(2, NOW, "kW"), state(2, self.now, "kW"))
        self.assertEqual(1, len(logs.records), "normal power changes must not add restore INFO logs")
        output = logs.output[0]
        for expected in ("from recorder", "12.500856 kWh", "sample_updated=", "attempts=1",
                         "total_elapsed=0.000 s", "recorder_queue_wait=0.250 s", "recorder_query_time=0.125 s"):
            self.assertIn(expected, output)

    async def test_timeout_log_and_recovery_include_retry_elapsed_and_keep_one_query(self):
        self.pending_future = asyncio.get_running_loop().create_future()
        entity = self.entity()
        with self.assertLogs(self.namespace["_LOGGER"], level="WARNING") as logs:
            await entity.async_added_to_hass()
        output = logs.output[0]
        for expected in ("TimeoutError", "retrying in 60 seconds", "attempt=1", "recorder_job_pending=True"):
            self.assertIn(expected, output)
        self.assertIsNone(entity.native_value)
        self.clock += 70
        self.pending_future.set_result(pv.RecorderRestore(self.recorded.sample, True, 8, 4))
        with self.assertLogs(self.namespace["_LOGGER"], level="INFO") as logs:
            self.timers[0].callback(self.now)
            await asyncio.gather(*self.tasks)
        output = logs.output[0]
        for expected in ("attempts=2", "total_elapsed=70.000 s", "recorder_queue_wait=8.000 s", "recorder_query_time=4.000 s"):
            self.assertIn(expected, output)
        self.assertEqual(1, len(self.executor_calls))
        self.assertEqual(Decimal("12.500856"), entity.native_value)

    async def test_manual_initialization_has_its_own_info_source(self):
        entity = self.entity()
        self.coordinator.hub.config_entry.data["values_to_init"] = {entity.entity_id: "0"}
        with self.assertLogs(self.namespace["_LOGGER"], level="INFO") as logs:
            await entity.async_added_to_hass()
        self.assertIn("from manual_config: 0 kWh", logs.output[0])
        self.assertEqual([], self.executor_calls)

    async def test_explicit_manual_reset_takes_priority_over_recorder(self):
        entity = self.entity()
        self.coordinator.hub.config_entry.data["values_to_init"] = {entity.entity_id: "0"}
        await entity.async_added_to_hass()
        self.assertEqual(Decimal(0), entity.native_value)
        self.assertEqual([], self.executor_calls)
        self.assertEqual({}, self.config_updates[0]["data"]["values_to_init"])

    async def test_existing_registry_entry_without_restore_data_remains_unknown(self):
        self.native = self.saved = None
        self.recorded = pv.RecorderRestore(None, False)
        entity = self.entity()
        await entity.async_added_to_hass()
        self.assertIsNone(entity.native_value)
        self.assertEqual([], self.listeners)
        self.assertEqual(60, self.timers[0].delay)

    async def test_genuinely_new_registry_counter_starts_at_zero(self):
        self.native = self.saved = None
        self.recorded = pv.RecorderRestore(None, False)
        self.registry_created = NOW + timedelta(microseconds=1)
        entity = self.entity()
        await entity.async_added_to_hass()
        self.assertEqual(Decimal(0), entity.native_value)
        self.assertEqual(1, len(self.listeners))

    async def test_invalid_existing_snapshot_does_not_initialize_zero(self):
        self.saved = state("unavailable")
        self.recorded = pv.RecorderRestore(None, True)
        entity = self.entity()
        await entity.async_added_to_hass()
        self.assertIsNone(entity.native_value)
        self.assertEqual([], self.writes)

    async def test_failed_recorder_stays_unknown_and_retries_without_integrating(self):
        self.worker_error = OSError("database not ready")
        entity = self.entity()
        await entity.async_added_to_hass()
        self.assertIsNone(entity.native_value)
        self.assertEqual([], self.listeners)
        entity._update_integral(Decimal(3600))
        self.assertIsNone(entity.native_value)
        retry = next(timer for timer in self.timers if timer.delay == 60)
        self.now += timedelta(minutes=1)
        self.worker_error = None
        retry.callback(self.now)
        await asyncio.gather(*self.tasks)
        self.assertEqual(Decimal("12.500856"), entity.native_value)
        self.assertEqual([Decimal("12.500856")], self.writes)
        self.assertEqual(self.now, entity._last_integration_time)
        self.assertEqual(1, len(self.listeners))

    async def test_timeout_reuses_single_executor_job_when_it_finishes_late(self):
        self.pending_future = asyncio.get_running_loop().create_future()
        entity = self.entity()
        await entity.async_added_to_hass()
        self.assertIsNone(entity.native_value)
        self.assertFalse(self.pending_future.cancelled())
        self.pending_future.set_result(self.recorded)
        self.timers[0].callback(self.now)
        await asyncio.gather(*self.tasks)
        self.assertEqual(1, len(self.executor_calls))
        self.assertEqual(Decimal("12.500856"), entity.native_value)

    async def test_removal_cancels_pending_read_and_prevents_late_retry(self):
        self.pending_future = asyncio.get_running_loop().create_future()
        entity = self.entity()
        await entity.async_added_to_hass()
        timer = self.timers[0]
        await entity.async_will_remove_from_hass()
        self.assertTrue(timer.cancel.cancelled)
        self.assertTrue(self.pending_future.cancelled())
        timer.callback(self.now)
        self.assertEqual([], self.tasks)
        self.assertIsNone(entity.native_value)

    async def test_daily_restore_crossing_midnight_starts_today_without_old_energy(self):
        zone = ZoneInfo("Europe/Stockholm")
        self.now = datetime(2026, 10, 18, 0, 1, tzinfo=zone)
        self.saved = state(12, self.now - timedelta(minutes=2))
        self.recorded = pv.RecorderRestore(pv.sample_from_state(self.saved), True)
        entity = self.entity(daily=True)
        await entity.async_added_to_hass()
        self.assertEqual(Decimal(0), entity.native_value)
        self.assertEqual(datetime(2026, 10, 19, 0, 0, tzinfo=zone), self.points[0].when)

    async def test_initial_event_does_not_integrate_time_before_restore_finished(self):
        entity = self.entity()
        await entity.async_added_to_hass()
        old = state(2, self.now - timedelta(minutes=30), "kW")
        new = state(2, self.now + timedelta(seconds=5), "kW")
        before = entity._state
        entity._integrate_on_state_change(old, new)
        self.assertAlmostEqual(Decimal(10) / Decimal(3600), entity._state - before)

    async def test_older_queued_event_cannot_reduce_the_integral(self):
        entity = self.entity()
        await entity.async_added_to_hass()
        before = entity._state
        entity._integrate_on_state_change(state(2, self.now - timedelta(minutes=2), "kW"),
                                         state(2, self.now - timedelta(minutes=1), "kW"))
        self.assertEqual(before, entity._state)

    async def test_midnight_schedule_respects_dst_and_does_not_reset_lifetime(self):
        zone = ZoneInfo("Europe/Stockholm")
        self.now = datetime(2026, 10, 25, 0, 30, tzinfo=zone)
        self.saved = state(12, self.now - timedelta(minutes=1))
        self.recorded = pv.RecorderRestore(pv.sample_from_state(self.saved), True)
        daily = self.entity(daily=True)
        await daily.async_added_to_hass()
        deadline = datetime(2026, 10, 26, 0, 0, tzinfo=zone)
        self.assertEqual(deadline, self.points[0].when)
        self.assertEqual(timedelta(hours=24, minutes=30),
                         deadline.astimezone(UTC) - self.now.astimezone(UTC))
        self.now = deadline
        self.points[0].callback(deadline)
        self.assertEqual(Decimal(0), daily.native_value)
        self.assertEqual(deadline.astimezone(UTC), daily._last_integration_time)
        self.points.clear()
        lifetime = self.entity()
        await lifetime.async_added_to_hass()
        self.assertEqual(Decimal(12), lifetime.native_value)
        self.assertEqual([], self.points)
