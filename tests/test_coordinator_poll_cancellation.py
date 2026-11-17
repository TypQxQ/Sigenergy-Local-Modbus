"""A cancelled or timed-out poll must stop every child device read.

Exercise the coordinator method with event-controlled collaborators, without
installing HA. The device layer separately verifies that cancelling a read
leaves its current wire transaction running safely to completion.
"""

from __future__ import annotations

import ast
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "sigen" / "coordinator.py"
)


class _UpdateFailed(Exception):
    pass


def _make_coordinator(*, ac_only: bool = False, timeout: float = 60,
                      poll_phase: bool = False, plant_value: object = 50):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(
        node for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_update_data"
    )
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "STARTUP_TIMEOUT": 180,
        "UPDATE_TIMEOUT": 60,
        "SLOW_POLL_WARNING_COUNT": 10,
        "async_timeout": SimpleNamespace(timeout=lambda _seconds: asyncio.timeout(timeout)),
        "dt_util": SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc)),
        "CONF_INVERTER_HAS_DCCHARGER": "has_dc_charger",
        "UpdateFailed": _UpdateFailed,
        "_LOGGER": Mock(),
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)

    child_tasks = set()
    all_started = asyncio.Event()
    release = asyncio.Event()
    events = []
    expected_reads = 2 if ac_only else 3

    async def read_plant():
        return {"plant_ess_soc": plant_value}

    async def read_device(name):
        child_tasks.add(asyncio.current_task())
        events.append(("started", name))
        if len(child_tasks) == expected_reads:
            all_started.set()
        try:
            await release.wait()
            events.append(("continued", name))
            return {"value": 1}
        finally:
            events.append(("finished", name))

    async def read_dc(name):
        return await read_device(f"{name} DC")

    hub = SimpleNamespace(
        begin_poll_update=lambda: None,
        inverter_connections={} if ac_only else {
            "inverter 1": {"has_dc_charger": True}, "inverter 2": {},
        },
        ac_charger_connections={"AC 1": {}, "AC 2": {}} if ac_only else {},
        async_read_plant_data=read_plant,
        async_read_inverter_data=read_device,
        async_read_dc_charger_data=read_dc,
        async_read_ac_charger_data=read_device,
        get_poll_diagnostics=lambda device: {},
    )
    coordinator = SimpleNamespace(
        hub=hub, data=None, largest_update_interval=0.0, latest_fetch_time=0.0,
        _update_sequence=int(poll_phase), _last_successful_update_sequence=int(poll_phase),
        _consecutive_slow_polls=0, update_interval=timedelta(seconds=5), last_update_success=True,
        update_timeout_count=0, update_failure_count=0,
        partial_update_count=0, _degraded_devices=set(),
    )
    coordinator.poll = MethodType(namespace["_async_update_data"], coordinator)
    return coordinator, child_tasks, all_started, release, events


class TestCoordinatorPollCancellation(unittest.IsolatedAsyncioTestCase):
    async def _assert_children_stop(self, *, ac_only=False, timeout=False):
        coordinator, children, started, release, events = _make_coordinator(
            ac_only=ac_only, timeout=0.05 if timeout else 60,
        )
        poll = asyncio.create_task(coordinator.poll())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            if timeout:
                with self.assertRaisesRegex(_UpdateFailed, "Timeout communicating"):
                    await poll
            else:
                poll.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await poll

            self.assertTrue(
                all(task.done() for task in children),
                "a sibling read outlived its cancelled coordinator poll",
            )
            self.assertFalse(any(event == "continued" for event, _ in events))
        finally:
            release.set()
            poll.cancel()
            for child in children:
                child.cancel()
            await asyncio.gather(poll, *children, return_exceptions=True)

    async def test_cancellation_stops_inverter_and_dc_siblings(self):
        await self._assert_children_stop()

    async def test_cancellation_stops_ac_charger_siblings(self):
        await self._assert_children_stop(ac_only=True)

    async def test_timeout_stops_inverter_and_dc_siblings(self):
        await self._assert_children_stop(timeout=True)

    async def test_timeout_stops_ac_charger_siblings(self):
        await self._assert_children_stop(ac_only=True, timeout=True)

    async def test_late_poll_timeout_publishes_readings_that_arrived(self):
        coordinator, children, _started, release, events = _make_coordinator(timeout=0.05, poll_phase=True)
        coordinator.hub.ac_charger_connections = {"AC 1": {}}
        try:
            data = await coordinator.poll()
            self.assertEqual({"plant_ess_soc": 50}, data["plant"])
            self.assertEqual({"inverter 1": {}, "inverter 2": {}}, data["inverters"])
            self.assertEqual({"inverter 1": {}}, data["dc_chargers"])
            self.assertEqual({"AC 1": {}}, data["ac_chargers"])
            self.assertNotIn(("started", "AC 1"), events)
            self.assertTrue(all(task.done() for task in children))
            timing = coordinator.latest_update_timing
            self.assertEqual("partial", timing["status"])
            for device in ("inverter:inverter 1", "inverter:inverter 2", "dc_charger:inverter 1", "ac_charger:AC 1"):
                self.assertEqual(("error", "TimeoutError"),
                                 (timing["devices"][device]["status"], timing["devices"][device]["error_type"]))
            self.assertEqual((1, 1, 0), (coordinator.update_timeout_count, coordinator.partial_update_count,
                                         coordinator.update_failure_count))
            self.assertEqual(coordinator._update_sequence, coordinator._last_successful_update_sequence)
        finally:
            release.set()
            await asyncio.gather(*children, return_exceptions=True)

    async def test_late_poll_timeout_without_readings_still_fails(self):
        coordinator, children, _started, release, _events = _make_coordinator(
            timeout=0.05, poll_phase=True, plant_value=None,
        )
        try:
            with self.assertRaisesRegex(_UpdateFailed, "Timeout communicating"):
                await coordinator.poll()
            self.assertEqual((1, 0, 1), (coordinator.update_timeout_count, coordinator.partial_update_count,
                                         coordinator.update_failure_count))
        finally:
            release.set()
            await asyncio.gather(*children, return_exceptions=True)

    async def test_successful_poll_preserves_all_device_results(self):
        coordinator, children, started, release, _events = _make_coordinator()
        poll = asyncio.create_task(coordinator.poll())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            release.set()
            data = await poll
            self.assertEqual({"plant_ess_soc": 50}, data["plant"])
            self.assertEqual({"inverter 1", "inverter 2"}, set(data["inverters"]))
            self.assertEqual({"inverter 1"}, set(data["dc_chargers"]))
            self.assertTrue(all(task.done() for task in children))
        finally:
            release.set()
            poll.cancel()
            await asyncio.gather(poll, *children, return_exceptions=True)
