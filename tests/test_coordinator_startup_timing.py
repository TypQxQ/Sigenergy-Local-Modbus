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


if __name__ == "__main__":
    unittest.main()
