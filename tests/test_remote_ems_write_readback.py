"""Remote EMS commands need a new post-write read, even during HA cooldown.

Run the complete coordinator class body against an event-controlled HA
coordinator stand-in. Its public refresh methods follow HA 2026.10's contract:
request_refresh may return after scheduling; async_refresh waits for the
refresh lock and performs a new update before notifying listeners.
"""

from __future__ import annotations

import ast
import asyncio
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE_PATH = Path(__file__).resolve().parents[1] / "custom_components/sigen/coordinator.py"
ENABLE = "plant_remote_ems_enable"
MODE = "plant_remote_ems_control_mode"


class _ModbusError(Exception):
    pass


class _UpdateFailed(Exception):
    pass


class _CoordinatorBase:
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.last_update_success = True
        self.last_exception = None
        self.refresh_lock = asyncio.Lock()
        self.deferred_requests = 0
        self.notifications = []
        self.shutdown_requested = False

    async def async_request_refresh(self):
        # Simulate HA's active cooldown: request accepted, read not run yet.
        self.deferred_requests += 1

    async def async_refresh(self):
        async with self.refresh_lock:
            if self.shutdown_requested:
                return
            try:
                self.data = await self._async_update_data()
            except _UpdateFailed as ex:
                self.last_exception = ex
                self.last_update_success = False
            else:
                self.last_update_success = True
            # Entity listeners see this same data before async_refresh returns.
            self.notifications.append(deepcopy(self.data))


class _Hub:
    def __init__(self):
        self.registers = {ENABLE: 0, MODE: 2}
        self.inverter_connections = {}
        self.ac_charger_connections = {}
        self.writes = []
        self.reads = 0
        self.tasks = set()
        self.read_started = asyncio.Event()
        self.write_finished = asyncio.Event()
        self.release_read = asyncio.Event()
        self.release_read.set()
        self.ignore_writes = False
        self.fail_read = False
        self.omit_registers = set()
        self.config_entry = SimpleNamespace(async_create_task=self.create_task)

    def create_task(self, hass, target, name=None):
        task = asyncio.create_task(target, name=name)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def get_poll_diagnostics(self, device):
        return {}

    async def async_write_parameter(self, *, device_type, device_identifier, register_name, value):
        self.writes.append((device_type, device_identifier, register_name, value))
        if not self.ignore_writes:
            self.registers[register_name] = value
        self.write_finished.set()

    async def async_read_plant_data(self):
        self.reads += 1
        # Snapshot at the beginning, so an older poll can finish after a write
        # while still carrying the old value. It must not confirm that write.
        snapshot = {
            key: value for key, value in self.registers.items()
            if key not in self.omit_registers
        }
        self.read_started.set()
        await self.release_read.wait()
        if self.fail_read:
            raise _ModbusError("link unavailable")
        return snapshot


def _make_coordinator():
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    constants = [
        node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in {
            "STARTUP_TIMEOUT", "UPDATE_TIMEOUT"
        } for target in node.targets)
    ]
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              *constants, cls],
        type_ignores=[],
    ))
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "async_timeout": SimpleNamespace(timeout=asyncio.timeout),
        "timedelta": timedelta,
        "DataUpdateCoordinator": _CoordinatorBase,
        "UpdateFailed": _UpdateFailed,
        "SigenergyModbusError": _ModbusError,
        "dt_util": SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc)),
        "DEFAULT_SCAN_INTERVAL": 5,
        "CONF_INVERTER_HAS_DCCHARGER": "has_dc_charger",
        "_LOGGER": Mock(),
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    hub = _Hub()
    coordinator = namespace["SigenergyDataUpdateCoordinator"](
        hass=object(), logger=Mock(), hub=hub, name="test", scan_interval=5,
    )
    coordinator.data = {"plant": dict(hub.registers)}
    return coordinator, hub, namespace


class TestRemoteEmsWriteReadback(unittest.IsolatedAsyncioTestCase):
    async def test_enable_finishes_with_new_readback_and_available_select(self):
        coordinator, hub, _namespace = _make_coordinator()
        await coordinator.async_write_parameter("plant", None, ENABLE, 1)

        self.assertEqual(1, hub.reads, "a deferred refresh request is not a readback")
        self.assertEqual(1, coordinator.data["plant"][ENABLE])
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual(1, coordinator.notifications[-1]["plant"][ENABLE])
        self.assertEqual(1, len(hub.writes))

    async def test_mode_confirmation_uses_new_read_even_when_cached_value_matches(self):
        coordinator, hub, _namespace = _make_coordinator()
        await coordinator.async_write_parameter("plant", None, MODE, 2)
        self.assertEqual(1, hub.reads)
        self.assertEqual(2, coordinator.data["plant"][MODE])

    async def test_old_in_flight_poll_cannot_confirm_the_enable_write(self):
        coordinator, hub, _namespace = _make_coordinator()
        hub.release_read.clear()
        old_poll = asyncio.create_task(coordinator.async_refresh())
        command = None
        try:
            await asyncio.wait_for(hub.read_started.wait(), timeout=1)
            command = asyncio.create_task(coordinator.async_write_parameter("plant", None, ENABLE, 1))
            await asyncio.wait_for(hub.write_finished.wait(), timeout=1)
            await asyncio.sleep(0)
            self.assertFalse(command.done(), "the command returned before a post-write poll")
            hub.release_read.set()
            await old_poll
            await command
        finally:
            hub.release_read.set()
            if command is not None:
                await asyncio.gather(command, return_exceptions=True)
            await asyncio.gather(old_poll, return_exceptions=True)

        self.assertEqual(2, hub.reads)
        self.assertEqual(0, coordinator.notifications[0]["plant"][ENABLE])
        self.assertEqual(1, coordinator.notifications[-1]["plant"][ENABLE])

    async def test_disabling_ems_also_reads_back_the_actual_state(self):
        coordinator, hub, _namespace = _make_coordinator()
        hub.registers[ENABLE] = 1
        coordinator.data["plant"][ENABLE] = 1
        await coordinator.async_write_parameter("plant", None, ENABLE, 0)
        self.assertEqual(0, coordinator.data["plant"][ENABLE])
        self.assertEqual(1, hub.reads)

    async def test_mismatch_reports_unconfirmed_write_without_replaying_it(self):
        coordinator, hub, _namespace = _make_coordinator()
        hub.ignore_writes = True
        with self.assertRaisesRegex(_ModbusError, "readback"):
            await coordinator.async_write_parameter("plant", None, MODE, 5)
        self.assertEqual(1, len(hub.writes))
        self.assertEqual(2, coordinator.data["plant"][MODE])

    async def test_missing_readback_is_not_treated_as_confirmation(self):
        coordinator, hub, _namespace = _make_coordinator()
        hub.omit_registers.add(ENABLE)
        with self.assertRaisesRegex(_ModbusError, "readback"):
            await coordinator.async_write_parameter("plant", None, ENABLE, 1)
        self.assertEqual(1, len(hub.writes))

    async def test_failed_poll_cannot_confirm_a_matching_cached_value(self):
        coordinator, hub, _namespace = _make_coordinator()
        coordinator.data["plant"][MODE] = 5
        hub.fail_read = True
        with self.assertRaisesRegex(_ModbusError, "readback"):
            await coordinator.async_write_parameter("plant", None, MODE, 5)
        self.assertEqual(1, len(hub.writes))

    async def test_shutdown_noop_cannot_confirm_a_matching_cached_value(self):
        coordinator, hub, _namespace = _make_coordinator()
        coordinator.data["plant"][MODE] = 5
        coordinator.shutdown_requested = True
        with self.assertRaisesRegex(_ModbusError, "readback"):
            await coordinator.async_write_parameter("plant", None, MODE, 5)
        self.assertEqual(1, len(hub.writes))

    async def test_readback_wait_is_bounded_and_does_not_repeat_the_write(self):
        coordinator, hub, namespace = _make_coordinator()
        hub.release_read.clear()
        namespace["async_timeout"].timeout = lambda _seconds: asyncio.timeout(0.01)
        try:
            with self.assertRaisesRegex(_ModbusError, "readback.*timed out"):
                await coordinator.async_write_parameter("plant", None, ENABLE, 1)
        finally:
            hub.release_read.set()
        self.assertEqual(1, len(hub.writes))

    async def test_caller_cancellation_does_not_abort_readback_or_log_unhandled_error(self):
        coordinator, hub, _namespace = _make_coordinator()
        errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_handler)
        hub.release_read.clear()
        command = asyncio.create_task(coordinator.async_write_parameter("plant", None, ENABLE, 1))
        try:
            await asyncio.wait_for(hub.read_started.wait(), timeout=1)
            command.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await command
        finally:
            hub.release_read.set()
            await asyncio.gather(command, *hub.tasks, return_exceptions=True)
        self.assertEqual(1, coordinator.data["plant"][ENABLE])
        self.assertEqual([], errors)
        self.assertEqual(1, len(hub.writes))

    async def test_unrelated_writes_keep_batched_refresh(self):
        coordinator, hub, _namespace = _make_coordinator()
        await coordinator.async_write_parameter("plant", None, "plant_active_power_fixed_target", 1)
        self.assertEqual(1, coordinator.deferred_requests)
        self.assertEqual(0, hub.reads)

    async def test_conflicting_commands_report_superseded_mode_without_replay(self):
        coordinator, hub, _namespace = _make_coordinator()
        both_written = asyncio.Event()
        original_write = hub.async_write_parameter

        async def write_and_notify(**kwargs):
            await original_write(**kwargs)
            if len(hub.writes) == 2:
                both_written.set()

        hub.async_write_parameter = write_and_notify
        await coordinator.refresh_lock.acquire()
        first = asyncio.create_task(coordinator.async_write_parameter("plant", None, MODE, 3))
        second = None
        try:
            await asyncio.wait_for(hub.write_finished.wait(), timeout=1)
            second = asyncio.create_task(coordinator.async_write_parameter("plant", None, MODE, 5))
            await asyncio.wait_for(both_written.wait(), timeout=1)
        finally:
            coordinator.refresh_lock.release()
            commands = [first] if second is None else [first, second]
            results = await asyncio.gather(*commands, return_exceptions=True)

        self.assertIsInstance(results[0], _ModbusError)
        self.assertIn("readback was 5, expected 3", str(results[0]))
        self.assertIsNone(results[1])
        self.assertEqual([3, 5], [write[-1] for write in hub.writes])
        self.assertEqual(5, coordinator.data["plant"][MODE])
        self.assertEqual(2, hub.reads)

    async def test_readback_timeout_waiting_for_lock_leaves_older_poll_running(self):
        coordinator, hub, namespace = _make_coordinator()
        hub.release_read.clear()
        old_poll = asyncio.create_task(coordinator.async_refresh())
        try:
            await asyncio.wait_for(hub.read_started.wait(), timeout=1)
            # The old poll already entered its original timeout context.
            # Shorten only the new command's confirmation budget.
            namespace["async_timeout"].timeout = lambda _seconds: asyncio.timeout(0.01)
            with self.assertRaisesRegex(_ModbusError, "readback.*timed out"):
                await coordinator.async_write_parameter("plant", None, ENABLE, 1)
            self.assertFalse(old_poll.done(), "confirmation must not cancel the refresh lock owner")
        finally:
            hub.release_read.set()
            await asyncio.gather(old_poll, return_exceptions=True)

        self.assertFalse(old_poll.cancelled())
        self.assertEqual(0, coordinator.data["plant"][ENABLE])
        await coordinator.async_refresh()
        self.assertEqual(1, coordinator.data["plant"][ENABLE])
        self.assertEqual(1, len(hub.writes))

    async def test_late_failed_readback_after_caller_cancellation_is_logged_once(self):
        coordinator, hub, namespace = _make_coordinator()
        errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_handler)
        hub.release_read.clear()
        hub.fail_read = True
        command = asyncio.create_task(coordinator.async_write_parameter("plant", None, ENABLE, 1))
        try:
            await asyncio.wait_for(hub.read_started.wait(), timeout=1)
            command.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await command
        finally:
            hub.release_read.set()
            await asyncio.gather(command, *hub.tasks, return_exceptions=True)
        await asyncio.sleep(0)

        logger = namespace["_LOGGER"]
        logger.error.assert_called_once()
        message = logger.error.call_args.args[0] % logger.error.call_args.args[1:]
        self.assertIn(ENABLE, message)
        self.assertIn("no fresh readback", message)
        self.assertFalse(coordinator.last_update_success)
        self.assertEqual(0, coordinator.data["plant"][ENABLE])
        self.assertEqual(1, len(hub.writes))
        self.assertEqual([], errors)
