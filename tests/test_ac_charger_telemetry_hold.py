"""Exercise EVAC poll failures through the shared endpoint and sensor path."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from modbus_connection import ModbusTimeoutError
from modbus_connection.exceptions import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection
from modbus_connection.model import Component, gauge

import test_enum_sensor_values as sensor_fixtures
import test_missing_control_readings as control_fixtures
from test_modbus_hub import _load_sigen_module
from test_remote_ems_write_readback import _make_coordinator

STATE = "ac_charger_system_state"
POWER = "ac_charger_charging_power"
CURRENT = "ac_charger_output_current"


class FixtureReadings(Component):
    register_space = "input"
    reading = gauge(30000, 1.0)


class TestAcChargerTelemetryHold(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sensor_fixture = sensor_fixtures.TestEnumSensorValues()
        cls.sensor_fixture.setUpClass()

    def setUp(self):
        devices = _load_sigen_module("sigen_devices")
        modbus = _load_sigen_module("modbus")
        connection = MockModbusConnection()
        self.endpoint = modbus._EndpointPoll(("10.12.0.9", 502), devices._EndpointWrites())
        self.unit = connection.for_unit(2)
        self.unit.load_raw({
            "input": dict.fromkeys(range(32000, 32015), 0) | {32000: 5},
            "holding": {42001: 0, 42002: 600},
        })
        charger = devices.SigenergyACCharger(self.unit)
        plant_unit = connection.for_unit(247)
        inverter_unit = connection.for_unit(1)
        groups = {"ac_charger:EVAC": charger._group}
        for name, unit in [("plant", plant_unit), ("inverter:inverter", inverter_unit)]:
            unit.load_raw({"input": {30000: 17}})
            groups[name] = devices._ComponentGroup({"readings": FixtureReadings(unit)}, {})
        for group in groups.values():
            support = {name: dict.fromkeys(component.declared_fields, True)
                       for name, component in group._readable.items()}
            group.enable_incremental(support, lambda: None, "test")
            group._endpoint_poll = self.endpoint

        self.coordinator, hub, self.namespace = _make_coordinator()
        hub.inverter_connections = {"inverter": {}}
        hub.ac_charger_connections = {"EVAC": {}}
        hub.begin_poll_update = self.endpoint.begin_update
        hub.finish_poll_update = self.endpoint.finish_update
        hub.async_read_plant_data = groups["plant"].async_update
        hub.async_read_inverter_data = lambda name: groups[f"inverter:{name}"].async_update()
        hub.async_read_ac_charger_data = lambda _name: charger.async_update()
        hub.get_poll_diagnostics = lambda name: groups[name].poll_diagnostics()
        self.state_sensor, _ = self.sensor_fixture.sensor(STATE)
        self.state_sensor._device_name = "EVAC"
        self.state_sensor.coordinator = self.coordinator

    async def test_one_miss_holds_state_during_early_publication_and_final_poll(self):
        await self.coordinator.async_refresh()
        self.assertEqual("charging", self.state_sensor.native_value)
        self.namespace["AC_CHARGER_PUBLISH_GRACE"] = 0
        self.endpoint.last_reconnect = self.namespace["time"].monotonic()
        early, release = asyncio.Event(), asyncio.Event()

        async def miss(_start, _count):
            await release.wait()
            raise ModbusTimeoutError("no answer")

        publish = self.coordinator.async_update_listeners

        def notify():
            publish()
            early.set()

        self.coordinator.async_update_listeners = notify
        with patch.object(self.unit, "read_input_registers", miss):
            refresh = asyncio.create_task(self.coordinator.async_refresh())
            try:
                await asyncio.wait_for(early.wait(), 1)
                self.assertFalse(refresh.done())
                self.assertEqual("charging", self.state_sensor.native_value)
                self.assertEqual({}, self.coordinator.data["ac_chargers"]["EVAC"])
            finally:
                release.set()
                await refresh

        self.assertEqual("charging", self.state_sensor.native_value)
        row = self.coordinator.data["ac_chargers"]["EVAC"]
        self.assertIsNone(row[STATE])
        self.assertEqual(6, row[CURRENT])
        self.assertEqual(0, self.coordinator.data["_ac_charger_held"]["EVAC"][POWER])
        quality = self.coordinator.latest_update_timing["devices"]["ac_charger:EVAC"]
        self.assertEqual("partial", quality["status"])
        self.assertIn(STATE, quality["held_fields"])
        self.assertEqual((32000, "ModbusTimeoutError"),
                         (quality["failed_blocks"][0]["address"], quality["failed_blocks"][0]["error_type"]))
        self.assertEqual((1, 0, 0), (self.endpoint.timeouts, self.endpoint.reconnects, self.endpoint.stops))

        self.unit.load_raw({"input": {32000: 1}})
        await self.coordinator.async_refresh()
        self.assertEqual("not_connected", self.state_sensor.native_value)
        self.assertNotIn("_ac_charger_held", self.coordinator.data)

    async def test_two_wire_timeouts_count_as_one_missed_poll_and_keep_siblings(self):
        await self.coordinator.async_refresh()
        self.unit.fail_read(32000, ModbusTimeoutError("no answer"), register_type="input")
        with patch.object(self.unit, "disconnect", new_callable=AsyncMock) as disconnect:
            await self.coordinator.async_refresh()
        disconnect.assert_awaited_once_with()
        self.assertEqual((2, 1, 1), (self.endpoint.timeouts, self.endpoint.reconnects, self.endpoint.stops))
        self.assertEqual({"reading": 17}, self.coordinator.data["plant"])
        self.assertEqual({"reading": 17}, self.coordinator.data["inverters"]["inverter"])
        self.assertEqual("charging", self.state_sensor.native_value)
        row = self.coordinator.data["ac_chargers"]["EVAC"]
        self.assertIsNone(row[STATE])
        self.assertIsNone(row[CURRENT])
        self.assertNotIn(CURRENT, self.coordinator.data["_ac_charger_held"]["EVAC"])
        number = control_fixtures.TestMissingControlReadings()
        number.setUp()
        current = number.entity("number", CURRENT, row, role="ac_charger", name="EVAC")
        current.coordinator = self.coordinator
        self.assertFalse(current.available)
        self.assertIsNone(current.native_value)
        self.assertEqual("EndpointStopped", next(
            block["error_type"] for block in
            self.coordinator.latest_update_timing["devices"]["ac_charger:EVAC"]["failed_blocks"]
            if block["address"] == 42001
        ))

        await self.coordinator.async_refresh()
        self.assertIsNone(self.state_sensor.native_value)
        self.assertNotIn("_ac_charger_held", self.coordinator.data)
        self.assertEqual(2, self.coordinator.partial_update_count)

    async def test_successful_reread_publishes_fresh_charger_value(self):
        await self.coordinator.async_refresh()
        reread = self.unit.read_input_registers
        self.unit.load_raw({"input": {32000: 4}})

        async def read(start, count):
            if self.endpoint.update_timeouts == 0:
                raise ModbusTimeoutError("missed first read")
            return await reread(start, count)

        with (patch.object(self.unit, "read_input_registers", read),
              patch.object(self.unit, "disconnect", new_callable=AsyncMock)):
            await self.coordinator.async_refresh()
        self.assertEqual("ev_ready", self.state_sensor.native_value)
        self.assertNotIn("_ac_charger_held", self.coordinator.data)
        self.assertEqual((1, 1, 0), (self.endpoint.timeouts, self.endpoint.successful_rereads, self.endpoint.stops))

    async def test_unsupported_read_is_not_held(self):
        await self.coordinator.async_refresh()
        self.unit.fail_read(32000, IllegalDataAddressError(2), register_type="input")
        await self.coordinator.async_refresh()
        self.assertIsNone(self.state_sensor.native_value)
        self.assertNotIn("_ac_charger_held", self.coordinator.data)

    async def test_poll_deadline_holds_once_and_still_reports_timeout(self):
        await self.coordinator.async_refresh()
        for state in ("charging", None):
            deadline = asyncio.timeout(None)
            self.namespace["async_timeout"].timeout = lambda _seconds: deadline

            async def miss_deadline(_name):
                deadline.reschedule(asyncio.get_running_loop().time())
                await asyncio.Future()

            self.coordinator.hub.async_read_ac_charger_data = miss_deadline
            await self.coordinator.async_refresh()
            self.assertEqual(state, self.state_sensor.native_value)
            self.assertTrue(self.coordinator.last_update_success)
            timing = self.coordinator.latest_update_timing["devices"]["ac_charger:EVAC"]
            self.assertEqual("TimeoutError", timing["error_type"])
            self.assertEqual(state is not None, STATE in timing.get("held_fields", ()))
        self.assertEqual(2, self.coordinator.update_timeout_count)
