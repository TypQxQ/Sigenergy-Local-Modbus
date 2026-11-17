"""Timeout recovery and diagnostics for the shared Modbus socket."""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, call, patch

from modbus_connection import ModbusConnectionError, ModbusError, ModbusTimeoutError
from modbus_connection.exceptions import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection
from modbus_connection.model import Component, gauge

from test_modbus_hub import _ConfigEntry, _load_sigen_module
from test_remote_ems_write_readback import _make_coordinator


class Readings(Component):
    register_space = "input"
    first = gauge(100, 1.0)
    middle = gauge(200, 1.0)
    last = gauge(300, 1.0)


class Commands(Component):
    register_space = "holding"
    set_output = gauge(400, 1.0, writable=True)


class TestPollTimeoutRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.modbus = _load_sigen_module("modbus")
        self.devices = _load_sigen_module("sigen_devices")
        self.connection = MockModbusConnection()
        self.endpoint = self.modbus._EndpointPoll(("10.12.0.9", 502), self.devices._EndpointWrites())
        self.unit = self.connection.for_unit(247)
        self.group = self.make_group(self.unit)
        self.endpoint.begin_update()

    def make_group(self, unit):
        unit.load_raw({"input": {100: 1, 200: 2, 300: 3}})
        group = self.devices._ComponentGroup({"readings": Readings(unit)}, {"commands": Commands(unit)})
        group.enable_incremental({}, lambda: None, "test")
        group._field_support["readings"].update(first=True, middle=True, last=True)
        group._endpoint_poll = self.endpoint
        group._endpoint_writes = self.endpoint.writes
        group._identity_snapshot = lambda: {"fixture": "stable"}
        group._confirm_endpoint_identity = AsyncMock(return_value=True)
        return group

    async def test_timeout_disconnects_and_commits_successful_reread(self):
        with (
            patch.object(self.unit, "read_input_registers", side_effect=[
                [1], ModbusTimeoutError("timed out"), [2], [3],
            ]) as read,
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
            self.assertLogs(self.modbus.__name__, "INFO") as logs,
        ):
            self.assertEqual({"first": 1, "middle": 2, "last": 3}, await self.group.async_update())
        disconnect.assert_awaited_once_with()
        self.assertEqual([call(100, 1), call(200, 1), call(200, 1), call(300, 1)], read.await_args_list)
        self.assertEqual("success", self.group.poll_diagnostics()["status"])
        self.assertEqual([], self.group.poll_diagnostics()["failed_blocks"])
        stats = self.endpoint.diagnostics()
        self.assertEqual((4, 1, 1, 1, 0), tuple(stats[k] for k in (
            "requests", "timeouts", "reconnects", "successful_rereads", "endpoint_stops",
        )))
        self.assertEqual(timedelta(0), datetime.fromisoformat(stats["last_reconnect"]).utcoffset())
        self.assertEqual(3, stats["request_latency_seconds"]["samples"])
        self.assertIn("10.12.0.9:502", logs.output[0])
        self.assertIn("address 200 (count 1)", logs.output[0])
        self.assertIn("recovered after reconnect", logs.output[1])

    async def test_retry_timeout_stops_endpoint_but_keeps_earlier_blocks(self):
        other_unit = self.connection.for_unit(1)
        other_group = self.make_group(other_unit)
        with (
            patch.object(self.unit, "read_input_registers", side_effect=[
                [1], ModbusTimeoutError("first"), ModbusTimeoutError("second"),
            ]) as read,
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
            patch.object(other_unit, "read_input_registers", wraps=other_unit.read_input_registers) as other_read,
        ):
            self.assertEqual({"first": 1, "middle": None, "last": None}, await self.group.async_update())
            self.assertEqual({"first": None, "middle": None, "last": None}, await other_group.async_update())
        disconnect.assert_awaited_once_with()
        self.assertEqual(3, read.await_count)
        other_read.assert_not_awaited()
        self.assertTrue(self.endpoint.stopped)
        self.assertEqual(1, self.endpoint.stops)
        blocks = self.group.poll_diagnostics()["failed_blocks"]
        self.assertEqual([(200, "read", "ModbusTimeoutError"), (300, "skipped", "_EndpointPollStopped")],
                         [(b["address"], b["stage"], b["error_type"]) for b in blocks])
        self.assertEqual(["first", "last", "middle"], other_group.poll_diagnostics()["failed_fields"])
        self.assertIsNone(self.group._read_round)
        self.group._confirm_endpoint_identity.assert_not_awaited()
        self.endpoint.begin_update()
        with patch.object(self.unit, "read_input_registers", wraps=self.unit.read_input_registers) as read:
            self.assertEqual({"first": 1, "middle": 2, "last": 3}, await self.group.async_update())
        self.assertEqual([call(100, 1), call(200, 1), call(300, 1)], read.await_args_list)

    async def test_cooldown_continues_after_first_failure_then_stops(self):
        with (
            patch.object(self.modbus, "time", SimpleNamespace(monotonic=lambda: 159.0, perf_counter=lambda: 0.0)),
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
            patch.object(self.unit, "read_input_registers", side_effect=[
                ModbusTimeoutError("first"), [2], ModbusTimeoutError("second"),
            ]) as read,
        ):
            self.endpoint.last_reconnect = 100.0
            self.assertEqual({"first": None, "middle": 2, "last": None}, await self.group.async_update())
        self.assertEqual([call(100, 1), call(200, 1), call(300, 1)], read.await_args_list)
        disconnect.assert_not_awaited()
        self.assertEqual((2, 1), (self.endpoint.timeouts, self.endpoint.stops))

    async def test_disconnect_error_is_logged_and_retry_still_runs(self):
        with (
            patch.object(self.unit, "read_input_registers", side_effect=[
                ModbusTimeoutError("timeout"), [1], [2], [3],
            ]) as read,
            patch.object(self.unit, "disconnect", side_effect=OSError("disconnect failed")) as disconnect,
            self.assertLogs(self.modbus.__name__, "DEBUG") as logs,
        ):
            self.assertEqual(1, (await self.group.async_update())["first"])
        self.assertEqual(4, read.await_count)
        disconnect.assert_awaited_once()
        self.assertIn("Could not disconnect", logs.output[0])
        self.assertEqual((0, 0), (self.endpoint.reconnects, self.endpoint.successful_rereads))
        self.assertIsNone(self.endpoint.last_reconnect_at)

    async def test_non_timeout_read_errors_do_not_disconnect(self):
        with (
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
            patch.object(self.unit, "read_input_registers", side_effect=[
                IllegalDataAddressError(2), ModbusError("gateway or protocol error"), [3],
                ModbusConnectionError("connection lost"),
            ]) as read,
        ):
            self.assertEqual({"first": None, "middle": None, "last": 3}, await self.group.async_update())
            with self.assertRaises(ModbusConnectionError):
                await self.group.async_update()
        self.assertEqual(4, read.await_count)
        disconnect.assert_not_awaited()
        self.assertEqual(0, self.endpoint.timeouts)

    async def test_probe_and_write_timeouts_are_outside_poll_recovery(self):
        with (
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
            patch.object(self.unit, "read_input_registers", side_effect=ModbusTimeoutError("probe timeout")) as read,
            patch.object(self.group._commands["commands"], "write", side_effect=ModbusTimeoutError("write timeout")) as write,
        ):
            component = self.group._readable["readings"]
            await self.group._async_read_blocks("readings", component, component.declared_fields, probing=True)
            with self.assertRaises(ModbusTimeoutError):
                await self.group.async_write("set_output", 4)
        self.assertEqual(3, read.await_count)
        write.assert_awaited_once_with("set_output", 4)
        disconnect.assert_not_awaited()
        self.assertEqual((0, 0), (self.endpoint.requests, self.endpoint.timeouts))

    async def test_disconnect_waits_for_a_write_on_the_same_endpoint(self):
        writer = self.make_group(self.connection.for_unit(1))
        write_started, first_read, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []

        async def write(register, value):
            write_started.set()
            await release.wait()
            events.append("write finished")

        async def read(address, count):
            if not first_read.is_set():
                first_read.set()
                raise ModbusTimeoutError("no reply")
            return [address // 100]

        async def disconnect():
            events.append("disconnect")

        with (
            patch.object(writer._commands["commands"], "write", write),
            patch.object(self.unit, "read_input_registers", read),
            patch.object(self.unit, "disconnect", disconnect),
        ):
            pending_write = asyncio.create_task(writer.async_write("set_output", 4))
            poll = None
            try:
                await asyncio.wait_for(write_started.wait(), 1)
                poll = asyncio.create_task(self.group.async_update())
                await asyncio.wait_for(first_read.wait(), 1)
                self.assertEqual([], events)
            finally:
                release.set()
                await asyncio.gather(*(t for t in (pending_write, poll) if t is not None))
        self.assertEqual(["write finished", "disconnect"], events)

    async def test_cancellation_during_retry_preserves_tracked_response(self):
        started, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def read(address, count):
            requests.append(address)
            if requests == [100]:
                raise ModbusTimeoutError("first")
            if address == 100:
                started.set()
                await release.wait()
            return [address // 100]

        with (
            patch.object(self.unit, "read_input_registers", read),
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
        ):
            task = asyncio.create_task(self.group.async_update())
            try:
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(1, len(self.group._pending_requests))
                self.endpoint.begin_update()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
            self.assertEqual({"first": 1, "middle": 2, "last": 3}, await self.group.async_update())
        self.assertEqual([100, 100, 200, 300], requests)
        disconnect.assert_awaited_once_with()
        self.assertFalse(self.group._pending_requests)
        self.assertFalse(self.endpoint.lock.locked())
        self.assertEqual((0, 1), (self.endpoint.update_timeouts, self.endpoint.successful_rereads))

    async def test_queued_group_cannot_send_after_endpoint_stops(self):
        other_unit = self.connection.for_unit(1)
        other_group = self.make_group(other_unit)
        retry_started, release = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def read(address, count):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                retry_started.set()
                await release.wait()
            raise ModbusTimeoutError("no reply")

        with (
            patch.object(self.unit, "read_input_registers", read),
            patch.object(self.unit, "disconnect", new_callable=AsyncMock),
            patch.object(other_unit, "read_input_registers", wraps=other_unit.read_input_registers) as sibling_read,
        ):
            first = asyncio.create_task(self.group.async_update())
            sibling = None
            try:
                await asyncio.wait_for(retry_started.wait(), 1)
                sibling = asyncio.create_task(other_group.async_update())
                await asyncio.sleep(0)
                sibling_read.assert_not_awaited()
            finally:
                release.set()
                await asyncio.gather(*(t for t in (first, sibling) if t is not None))
        sibling_read.assert_not_awaited()
        self.assertEqual(2, attempts)

    async def test_cancelled_queued_block_is_read_in_the_next_update(self):
        other_unit = self.connection.for_unit(1)
        other_group = self.make_group(other_unit)
        with patch.object(other_unit, "read_input_registers", wraps=other_unit.read_input_registers) as read:
            async with self.endpoint.lock:
                task = asyncio.create_task(other_group.async_update())
                try:
                    for _ in range(5):
                        if other_group._pending_requests:
                            break
                        await asyncio.sleep(0)
                    self.assertEqual(1, len(other_group._pending_requests))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.endpoint.begin_update()
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual({"first": 1, "middle": 2, "last": 3}, await other_group.async_update())
        self.assertEqual([call(100, 1), call(200, 1), call(300, 1)], read.await_args_list)
        self.assertFalse(other_group._pending_requests)

    async def test_hub_groups_share_count_and_cooldown_and_coordinator_resets_count(self):
        const = _load_sigen_module("const")
        plant = {const.CONF_HOST: "SIGEN.local", const.CONF_PORT: 502, const.CONF_SLAVE_ID: 247}
        inverter = {const.CONF_HOST: "sigen.local", const.CONF_PORT: 502, const.CONF_SLAVE_ID: 1}
        entry = _ConfigEntry({
            const.CONF_PLANT_CONNECTION: plant,
            const.CONF_INVERTER_CONNECTIONS: {"inv": inverter},
        })
        inverter_unit = self.connection.for_unit(1)
        with patch.object(self.modbus, "_acquire_unit", side_effect=[self.unit, inverter_unit]):
            hub = self.modbus.SigenergyModbusHub(None, entry)
        shared = hub._plant._group._endpoint_poll
        self.assertIs(shared, hub._inverters["inv"]._inverter_group._endpoint_poll)
        self.endpoint = shared
        hub._plant._group = self.make_group(self.unit)
        hub._inverters["inv"]._inverter_group = self.make_group(inverter_unit)
        coordinator, _, _ = _make_coordinator()
        coordinator.hub = hub

        with (
            patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect,
            patch.object(inverter_unit, "disconnect", new_callable=AsyncMock) as inverter_disconnect,
            patch.object(self.unit, "read_input_registers", side_effect=[
                ModbusTimeoutError("reconnect"), [1], [2], [3],
                ModbusTimeoutError("cooldown"), [2], [3],
                ModbusTimeoutError("new update"), [2], [3],
            ]),
            patch.object(inverter_unit, "read_input_registers", side_effect=[
                [1], [2], [3], ModbusTimeoutError("second unit"), [1], [2], [3],
            ]),
        ):
            await coordinator._async_update_data()
            self.assertEqual(1, shared.update_timeouts)
            snapshot = await coordinator._async_update_data()
            self.assertEqual(2, shared.update_timeouts)
            self.assertEqual(2, snapshot["plant"]["middle"])
            self.assertIsNone(snapshot["inverters"]["inv"]["middle"])
            snapshot = await coordinator._async_update_data()
            self.assertEqual(1, shared.update_timeouts)
            self.assertEqual(2, snapshot["inverters"]["inv"]["middle"])
        disconnect.assert_awaited_once_with()
        inverter_disconnect.assert_not_awaited()
        self.assertEqual(1, shared.stops)
        self.assertEqual(1, len(hub.poll_endpoint_diagnostics()))

    def test_diagnostics_redact_endpoint_host_and_report_bounded_latency_percentiles(self):
        self.endpoint.latencies.extend(range(1, 206))
        diagnostics = Path(__file__).resolve().parents[1] / "custom_components/sigen/diagnostics.py"
        tree = ast.parse(diagnostics.read_text(encoding="utf-8"))
        endpoint_values = [value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                           for key, value in zip(node.keys, node.values)
                           if isinstance(key, ast.Constant) and key.value == "modbus_poll_endpoints"]
        self.assertEqual(2, len(endpoint_values))

        def redact(data, keys):
            if isinstance(data, list):
                return [redact(value, keys) for value in data]
            if isinstance(data, dict):
                return {key: "**REDACTED**" if key in keys else redact(value, keys)
                        for key, value in data.items()}
            return data

        hub = SimpleNamespace(poll_endpoint_diagnostics=lambda: [self.endpoint.diagnostics()])
        for node in endpoint_values:
            result, = eval(compile(ast.Expression(node), str(diagnostics), "eval"),
                           {"hub": hub, "TO_REDACT": {"host"}, "async_redact_data": redact})
            self.assertNotIn("10.12.0.9", json.dumps(result))
            self.assertEqual({"host": "**REDACTED**", "port": 502}, result["endpoint"])
            self.assertEqual({"samples": 200, "p50": 105, "p90": 185, "max": 205},
                             result["request_latency_seconds"])


class TestSlowPollWarning(unittest.IsolatedAsyncioTestCase):
    async def test_warns_after_ten_slow_polls_and_logs_recovery_once(self):
        coordinator, hub, namespace = _make_coordinator()
        clock = [0.0]
        duration = [6.0]
        read = hub.async_read_plant_data

        async def timed_read():
            clock[0] += duration[0]
            return await read()

        namespace["time"] = SimpleNamespace(perf_counter=lambda: clock[0])
        hub.async_read_plant_data = timed_read
        logger = namespace["_LOGGER"]
        await coordinator._async_update_data()  # startup
        for _ in range(9):
            await coordinator._async_update_data()
        logger.warning.assert_not_called()
        for _ in range(3):
            await coordinator._async_update_data()
        logger.warning.assert_called_once()
        self.assertIn("longer polling interval", logger.warning.call_args.args[0])
        duration[0] = 4.0
        await coordinator._async_update_data()
        await coordinator._async_update_data()
        logger.info.assert_called_once()
