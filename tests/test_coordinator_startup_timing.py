"""Startup deadlines, recovery and monotonic diagnostics without a HA install."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_remote_ems_write_readback import _make_coordinator


class TestCoordinatorStartupTiming(unittest.IsolatedAsyncioTestCase):
    async def test_slow_startup_succeeds_but_same_runtime_read_times_out(self):
        coordinator, hub, namespace = _make_coordinator()
        budgets = []

        def timeout(seconds):
            budgets.append(seconds)
            # Scale 180/60 seconds to keep this real timeout test fast.
            return asyncio.timeout(seconds / 1000)

        namespace["async_timeout"].timeout = timeout

        async def slow_plant():
            await asyncio.sleep(0.09)
            return {"soc": 50}

        hub.async_read_plant_data = slow_plant
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual(180, coordinator.latest_update_timing["timeout"])
        self.assertEqual("startup", coordinator.latest_update_timing["phase"])
        startup_time = coordinator.startup_fetch_time
        self.assertGreater(startup_time, 0.06)

        await coordinator.async_refresh()
        self.assertFalse(coordinator.last_update_success)
        self.assertEqual("timeout", coordinator.latest_update_timing["status"])
        self.assertEqual("poll", coordinator.latest_update_timing["phase"])
        self.assertEqual("cancelled", coordinator.latest_update_timing["devices"]["plant"]["status"])
        self.assertEqual(1, coordinator.update_timeout_count)
        self.assertEqual(1, coordinator.update_failure_count)
        self.assertGreater(coordinator.latest_fetch_time, 0)
        self.assertEqual(startup_time, coordinator.startup_fetch_time)

        async def recovered_plant():
            return {"soc": 51}

        hub.async_read_plant_data = recovered_plant
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual([180, 60, 60], budgets)
        self.assertEqual(51, coordinator.data["plant"]["soc"])
        self.assertEqual(1, coordinator.update_timeout_count)

    async def test_failed_first_attempt_keeps_startup_budget_for_retry(self):
        coordinator, hub, namespace = _make_coordinator()
        budgets = []

        def timeout(seconds):
            budgets.append(seconds)
            return asyncio.timeout(0.02)

        namespace["async_timeout"].timeout = timeout
        hub.release_read.clear()
        await coordinator.async_refresh()
        self.assertFalse(coordinator.last_update_success)
        self.assertIsNone(coordinator.startup_fetch_time)
        self.assertEqual(0, coordinator._last_successful_update_sequence)
        self.assertEqual("timeout", coordinator.latest_update_timing["status"])

        hub.release_read.set()
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual([180, 180], budgets)
        self.assertIsNotNone(coordinator.startup_fetch_time)

    async def test_duration_ignores_wall_clock_and_failure_is_measured(self):
        coordinator, hub, namespace = _make_coordinator()
        clock = [100.0]
        namespace["time"] = SimpleNamespace(perf_counter=lambda: clock[0])
        wall_clock = Mock(side_effect=AssertionError("wall clock must not measure duration"))
        namespace["dt_util"] = SimpleNamespace(utcnow=wall_clock)

        async def failed_plant():
            clock[0] += 5.0
            raise RuntimeError("link unavailable")

        hub.async_read_plant_data = failed_plant
        await coordinator.async_refresh()
        self.assertFalse(coordinator.last_update_success)
        self.assertEqual(5.0, coordinator.latest_fetch_time)
        self.assertEqual(5.0, coordinator.largest_update_interval)
        self.assertEqual(5.0, coordinator.latest_update_timing["devices"]["plant"]["duration"])
        self.assertEqual("error", coordinator.latest_update_timing["status"])
        self.assertEqual(1, coordinator.update_failure_count)
        self.assertEqual(0, coordinator.update_timeout_count)
        wall_clock.assert_not_called()

    async def test_each_device_group_is_timed_separately(self):
        coordinator, hub, _namespace = _make_coordinator()
        hub.inverter_connections = {"inverter": {"has_dc_charger": True}}
        hub.ac_charger_connections = {"AC": {}}

        async def read(_name):
            return {"value": 1}

        hub.async_read_inverter_data = read
        hub.async_read_dc_charger_data = read
        hub.async_read_ac_charger_data = read
        await coordinator.async_refresh()
        timings = coordinator.latest_update_timing["devices"]
        self.assertEqual({"plant", "inverter:inverter", "dc_charger:inverter", "ac_charger:AC"}, set(timings))
        self.assertTrue(all(item["status"] == "success" for item in timings.values()))
        self.assertTrue(all(item["duration"] >= 0 for item in timings.values()))


class TestAcChargerPolling(unittest.IsolatedAsyncioTestCase):
    async def test_publish_healthy_data_while_charger_waits_and_retry_after_delay(self):
        coordinator, hub, namespace = _make_coordinator()
        namespace["AC_CHARGER_PUBLISH_GRACE"] = 0
        await coordinator.async_refresh()
        hub.inverter_connections = {"inverter": {}}
        hub.ac_charger_connections = {"slow": {}, "healthy": {}}
        hub.registers["soc"] = 51
        hub.async_read_inverter_data = self._read_inverter
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()
        attempts = []
        timed_out = True

        async def read_charger(name):
            attempts.append(name)
            if name == "slow" and timed_out:
                slow_started.set()
                await release_slow.wait()
                return {"current_limit": 6}
            return {"ac_charger_charging_power": 2}

        def quality(device):
            if device == "ac_charger:slow" and timed_out:
                return {
                    "status": "partial",
                    "failed_blocks": [{"error_type": "ModbusTimeoutError"}],
                }
            return {}

        hub.async_read_ac_charger_data = read_charger
        hub.get_poll_diagnostics = quality
        seen_early = asyncio.Event()
        real_update_listeners = coordinator.async_update_listeners

        def notify():
            real_update_listeners()
            seen_early.set()

        coordinator.async_update_listeners = notify
        refresh = asyncio.create_task(coordinator.async_refresh())
        try:
            await asyncio.wait_for(seen_early.wait(), 1)
            self.assertTrue(slow_started.is_set())
            self.assertFalse(refresh.done())
            early = coordinator.notifications[-1]
            self.assertEqual(51, early["plant"]["soc"])
            self.assertEqual({"power": 3}, early["inverters"]["inverter"])
            self.assertEqual({}, early["ac_chargers"]["slow"])
            self.assertEqual({"ac_charger_charging_power": 2}, early["ac_chargers"]["healthy"])
            coordinator.mark_sensors_initialized()
            release_slow.set()
            await refresh
        finally:
            release_slow.set()
            await asyncio.gather(refresh, return_exceptions=True)

        self.assertEqual({"current_limit": 6}, coordinator.data["ac_chargers"]["slow"])
        self.assertTrue(coordinator.data["_sensors_initialized"])
        hub.registers["soc"] = 52
        await coordinator.async_refresh()
        self.assertEqual(52, coordinator.data["plant"]["soc"])
        self.assertEqual({}, coordinator.data["ac_chargers"]["slow"])
        self.assertEqual("deferred", coordinator.latest_update_timing["devices"]["ac_charger:slow"]["status"])
        self.assertEqual(["slow", "healthy", "healthy"], attempts)

        timed_out = False
        coordinator._ac_charger_retry_at["slow"] = 0
        await coordinator.async_refresh()
        self.assertEqual({"ac_charger_charging_power": 2}, coordinator.data["ac_chargers"]["slow"])
        self.assertNotIn("slow", coordinator._ac_charger_retry_at)
        self.assertEqual("success", coordinator.latest_update_timing["status"])

    async def test_charger_deadline_preserves_healthy_data_and_defers_each_failed_charger(self):
        coordinator, hub, namespace = _make_coordinator()
        await coordinator.async_refresh()
        namespace["AC_CHARGER_PUBLISH_GRACE"] = 0
        namespace["async_timeout"].timeout = lambda _seconds: asyncio.timeout(0.05)
        hub.ac_charger_connections = {
            "stalled": {}, "timed out": {}, "disconnected": {}, "healthy": {},
        }
        hub.registers["soc"] = 51
        cancelled = asyncio.Event()
        attempts = []

        async def read_charger(name):
            attempts.append(name)
            if name == "stalled":
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            if name == "disconnected":
                raise ModbusConnectionError()
            return {"ac_charger_charging_power": 2} if name == "healthy" else {}

        class ModbusConnectionError(Exception):
            pass

        def quality(device):
            if device == "ac_charger:timed out":
                return {
                    "status": "partial",
                    "failed_blocks": [{"error_type": "ModbusTimeoutError"}],
                }
            return {}

        hub.async_read_ac_charger_data = read_charger
        hub.get_poll_diagnostics = quality
        await coordinator.async_refresh()
        self.assertTrue(cancelled.is_set())
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual(51, coordinator.data["plant"]["soc"])
        self.assertEqual({}, coordinator.data["ac_chargers"]["stalled"])
        self.assertEqual({"ac_charger_charging_power": 2}, coordinator.data["ac_chargers"]["healthy"])
        self.assertEqual("partial", coordinator.latest_update_timing["status"])
        self.assertEqual({"stalled", "timed out", "disconnected"}, set(coordinator._ac_charger_retry_at))

        hub.registers["soc"] = 52
        await coordinator.async_refresh()
        self.assertEqual(52, coordinator.data["plant"]["soc"])
        self.assertEqual(["stalled", "timed out", "disconnected", "healthy", "healthy"], attempts)

    @staticmethod
    async def _read_inverter(_name):
        return {"power": 3}


if __name__ == "__main__":
    unittest.main()
