"""Partial reads must remain usable and distinguishable from healthy polls."""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from modbus_connection import ModbusTimeoutError
from modbus_connection.exceptions import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection
from modbus_connection.model import Component, gauge

from test_modbus_hub import _load_sigen_module
from test_remote_ems_write_readback import _make_coordinator


class TestDevicePollQuality(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        devices = _load_sigen_module("sigen_devices")
        self.unit = MockModbusConnection().for_unit(1)
        self.unit.load_raw({"input": {100: 123, 101: 456, 500: 789}})
        self.component = type("QualityFixture", (Component,), {
            "register_space": "input", "first": gauge(100, 1.0),
            "second": gauge(101, 1.0), "separate": gauge(500, 1.0),
        })(self.unit)
        self.group = devices._ComponentGroup({"telemetry": self.component}, {})
        self.group.enable_incremental({}, lambda: None, "hit")

    async def test_unknown_and_unsupported_are_not_partial_read_failures(self):
        self.group._field_support["telemetry"].update(first=True, second=False)
        self.group.queue_discovery()
        self.unit.fail_read(101, IllegalDataAddressError(2), register_type="input")
        values = await self.group.async_update()
        self.assertEqual(123, values["first"])
        self.assertIsNone(values["second"])
        self.assertIsNone(values["separate"])
        # Negative capability rechecks and unknown discovery have their own
        # counters; an expected unsupported register is not a failed live read.
        await self.group.async_discover_one()
        quality = self.group.poll_diagnostics()
        self.assertEqual("success", quality["status"])
        self.assertEqual(0, quality["partial_update_count"])
        self.assertEqual([], quality["failed_blocks"])

    async def test_failed_supported_block_is_counted_and_recovery_retains_evidence(self):
        self.group._field_support["telemetry"].update(first=True, second=True, separate=True)
        self.unit.fail_read(100, ModbusTimeoutError("private raw message"), register_type="input")
        for expected_count in (1, 2):
            values = await self.group.async_update()
            self.assertIsNone(values["first"])
            self.assertIsNone(values["second"])
            self.assertEqual(789, values["separate"])
            quality = self.group.poll_diagnostics()
            self.assertEqual("partial", quality["status"])
            self.assertEqual(expected_count, quality["partial_update_count"])
            self.assertEqual(["first", "second"], quality["failed_fields"])
            self.assertEqual([{
                "component": "telemetry", "register_space": "input",
                "address": 100, "count": 2, "stage": "read",
                "fields": ["first", "second"], "error_type": "ModbusTimeoutError",
            }], quality["failed_blocks"])
            self.assertIs(self.group.is_supported("first"), True)
            self.assertNotIn("private raw", json.dumps(quality))
        previous_failure = quality["last_failure"]
        self.unit.fail_read(100, None, register_type="input")
        self.assertEqual(123, (await self.group.async_update())["first"])
        quality = self.group.poll_diagnostics()
        self.assertEqual("success", quality["status"])
        self.assertEqual(2, quality["partial_update_count"])
        self.assertEqual([], quality["failed_fields"])
        self.assertEqual(previous_failure, quality["last_failure"])
        self.assertIsNotNone(quality["last_recovery"])
        quality["last_failure"]["failed_blocks"].clear()
        self.assertTrue(self.group.poll_diagnostics()["last_failure"]["failed_blocks"])

    async def test_previously_supported_illegal_address_is_reported_before_rediscovery(self):
        self.group._field_support["telemetry"].update(first=True, second=True, separate=True)
        self.unit.fail_read(101, IllegalDataAddressError(2), register_type="input")
        await self.group.async_update()
        self.assertEqual("partial", self.group.poll_diagnostics()["status"])
        self.assertIsNone(self.group.is_supported("first"))
        self.assertIsNone(self.group.is_supported("second"))
        # Suspended fields are no longer attempted as known supported data.
        await self.group.async_update()
        self.assertEqual("success", self.group.poll_diagnostics()["status"])
        self.assertEqual(1, self.group.poll_diagnostics()["partial_update_count"])

    async def test_short_supported_response_is_a_decode_failure_not_zero(self):
        self.group._field_support["telemetry"].update(first=True, second=True, separate=True)
        original = self.unit.read_input_registers

        async def truncated(start, count):
            return [123] if start == 100 else await original(start, count)

        with patch.object(self.unit, "read_input_registers", truncated):
            values = await self.group.async_update()
        self.assertEqual(123, values["first"])
        self.assertIsNone(values["second"])
        self.assertEqual(789, values["separate"])
        block = self.group.poll_diagnostics()["failed_blocks"][0]
        self.assertEqual("decode", block["stage"])
        self.assertEqual(["second"], block["fields"])
        self.assertEqual("ValueError", block["error_type"])

    async def test_cancelled_round_retains_late_failure_until_resume(self):
        self.group._field_support["telemetry"].update(first=True, second=True, separate=True)
        self.group._identity_snapshot = lambda: {"serial": "same"}
        self.group._confirm_endpoint_identity = AsyncMock(return_value=True)
        started, release = asyncio.Event(), asyncio.Event()
        original = self.unit.read_input_registers

        async def slow_failure(start, count):
            if start == 100:
                started.set()
                await release.wait()
                raise ModbusTimeoutError("late failure")
            return await original(start, count)

        with patch.object(self.unit, "read_input_registers", slow_failure):
            task = asyncio.create_task(self.group.async_update())
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(0, self.group.poll_diagnostics()["partial_update_count"])
            release.set()
            values = await self.group.async_update()
        self.assertIsNone(values["first"])
        self.assertEqual(789, values["separate"])
        self.assertEqual(1, self.group.poll_diagnostics()["partial_update_count"])
        self.assertEqual(["first", "second"], self.group.poll_diagnostics()["failed_fields"])
        self.assertEqual(123, (await self.group.async_update())["first"])
        self.assertEqual("success", self.group.poll_diagnostics()["status"])


class TestCoordinatorPollQuality(unittest.IsolatedAsyncioTestCase):
    async def test_partial_poll_keeps_good_values_and_logs_only_transitions(self):
        coordinator, hub, namespace = _make_coordinator()
        quality = {"status": "partial", "failed_fields": ["missing"],
                   "failed_blocks": [{"address": 100, "error_type": "ModbusTimeoutError"}]}
        hub.get_poll_diagnostics = lambda device: dict(quality)
        for expected_count in (1, 2):
            await coordinator.async_refresh()
            self.assertTrue(coordinator.last_update_success)
            self.assertEqual(0, coordinator.data["plant"]["plant_remote_ems_enable"])
            self.assertEqual("partial", coordinator.latest_update_timing["status"])
            self.assertEqual(expected_count, coordinator.partial_update_count)
            self.assertEqual(0, coordinator.update_failure_count)
            self.assertEqual(0, coordinator.update_timeout_count)
        self.assertEqual(1, namespace["_LOGGER"].warning.call_count)
        self.assertIsNotNone(coordinator.startup_fetch_time)
        quality.update(status="success", failed_fields=[], failed_blocks=[])
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        self.assertEqual("success", coordinator.latest_update_timing["status"])
        self.assertEqual(2, coordinator.partial_update_count)
        self.assertEqual(1, namespace["_LOGGER"].info.call_count)
        quality["status"] = "partial"
        await coordinator.async_refresh()
        self.assertEqual(2, namespace["_LOGGER"].warning.call_count)

    async def test_one_failed_device_is_partial_and_preserves_healthy_devices(self):
        coordinator, hub, _namespace = _make_coordinator()
        hub.inverter_connections = {"bad": {}, "good": {}}

        async def read(name):
            if name == "bad":
                raise OSError("private error")
            return {"power": 123}

        hub.async_read_inverter_data = read
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual({}, coordinator.data["inverters"]["bad"])
        self.assertEqual(123, coordinator.data["inverters"]["good"]["power"])
        self.assertEqual("partial", coordinator.latest_update_timing["status"])
        self.assertEqual("error", coordinator.latest_update_timing["devices"]["inverter:bad"]["status"])
        self.assertEqual(1, coordinator.partial_update_count)
        self.assertNotIn("private error", json.dumps(coordinator.latest_update_timing))

    async def test_explicit_error_causes_are_retained_without_private_messages(self):
        for cyclic in (False, True):
            with self.subTest(cyclic=cyclic):
                coordinator, hub, namespace = _make_coordinator()
                hub.inverter_connections = {"bad": {}}
                root = ConnectionError("private endpoint")
                middle = OSError("private payload")
                middle.__cause__ = root
                outer = RuntimeError("private register value")
                outer.__cause__ = middle
                if cyclic:
                    root.__cause__ = outer

                async def failed(name):
                    raise outer

                hub.async_read_inverter_data = failed
                await coordinator.async_refresh()
                timing = coordinator.latest_update_timing["devices"]["inverter:bad"]
                self.assertEqual("RuntimeError", timing["error_type"])
                self.assertEqual("ConnectionError", timing["cause_type"])
                self.assertEqual("partial", coordinator.latest_update_timing["status"])
                self.assertNotIn("private", json.dumps(timing))
                self.assertNotIn("private", str(namespace["_LOGGER"].warning.call_args))

    async def test_pv_restore_diagnostics_show_live_ages_and_serialize_exact_values(self):
        coordinator, _hub, namespace = _make_coordinator()
        namespace["time"] = SimpleNamespace(monotonic=lambda: 120)
        coordinator.pv_restore_states["sensor.pv"] = {
            "status": "waiting_for_recorder", "attempts": 1,
            "_started_at": 100, "_job_started_at": 101,
            "recorder_job_pending": True, "native_value_kwh": None,
        }
        row = coordinator.pv_restore_diagnostics()["sensor.pv"]
        self.assertEqual(20, row["total_elapsed"])
        self.assertEqual(19, row["recorder_job_age"])
        self.assertFalse(any(key.startswith("_") for key in row))
        coordinator.pv_restore_states["sensor.pv"].update(
            status="restored", source="snapshot", native_value_kwh="6333.170261123",
            _started_at=None, _job_started_at=None, total_elapsed=22,
        )
        row = coordinator.pv_restore_diagnostics()["sensor.pv"]
        self.assertEqual(22, row["total_elapsed"])
        self.assertIsNone(row["recorder_job_age"])
        self.assertIn("6333.170261123", json.dumps(row))


if __name__ == "__main__":
    unittest.main()
