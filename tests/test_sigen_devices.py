"""Regression tests for probing, batching and cancellation-safe requests.

Uses modbus_connection's own in-memory mock backend (MockModbusConnection)
to exercise the real Component/field machinery end to end, rather than
re-implementing it. Skips entirely if modbus-connection is not installed.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "sigen_devices.py"
)

try:
    from modbus_connection import ModbusConnectionError, ModbusTimeoutError
    from modbus_connection.exceptions import IllegalDataAddressError
    from modbus_connection.mock import MockModbusConnection
except ImportError:
    if os.environ.get("SIGEN_REQUIRE_MODBUS_CONNECTION") == "1":
        raise
    _HAVE_MODBUS_CONNECTION = False
else:
    _HAVE_MODBUS_CONNECTION = True


class _AnyAttr:
    def __getattr__(self, name: str) -> str:
        return f"stub:{name}"


def _install_stubs() -> None:
    if "homeassistant.const" in sys.modules:
        return
    ha = types.ModuleType("homeassistant")
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.PERCENTAGE = "%"
    for name in (
        "UnitOfElectricCurrent",
        "UnitOfElectricPotential",
        "UnitOfEnergy",
        "UnitOfFrequency",
        "UnitOfPower",
        "UnitOfTemperature",
    ):
        setattr(ha_const, name, _AnyAttr())
    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.const"] = ha_const


def _load_sigen_module(name: str):
    _install_stubs()
    if "sigen" not in sys.modules:
        package = types.ModuleType("sigen")
        package.__path__ = [str(SOURCE_PATH.parent)]
        sys.modules["sigen"] = package
    return importlib.import_module(f"sigen.{name}")


def _load_zeroes(unit, space: str, registers, *, skip_write_only: bool = False) -> None:
    for reg in registers.values():
        if skip_write_only and reg.register_type.value == "wo":
            continue
        values = {reg.address: 0}
        if reg.count > 1:
            values.update({reg.address + i: 0 for i in range(1, reg.count)})
        unit.load_raw({space: values})


@unittest.skipUnless(_HAVE_MODBUS_CONNECTION, "modbus-connection is not installed")
class TestSigenergyPlant(unittest.IsolatedAsyncioTestCase):
    """Probing, narrowing, decoding and write dispatch for the plant device."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.devices = _load_sigen_module("sigen_devices")
        cls.defs = _load_sigen_module("modbusregisterdefinitions")

    def _new_plant(self):
        conn = MockModbusConnection()
        unit = conn.for_unit(1)
        _load_zeroes(unit, "input", self.defs.PLANT_RUNNING_INFO_REGISTERS)
        _load_zeroes(unit, "holding", self.defs.PLANT_PARAMETER_REGISTERS, skip_write_only=True)
        _load_zeroes(unit, "holding", self.defs.PLANT_ESS_PREHEATING_REGISTERS)
        return unit, self.devices.SigenergyPlant(unit)

    async def test_narrows_around_an_unsupported_register_and_decodes_gain(self) -> None:
        unit, plant = self._new_plant()
        soh_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soh"].address
        unit.fail_read(soh_addr, IllegalDataAddressError(2), register_type="input")
        soc_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soc"].address
        unit.load_raw({"input": {soc_addr: 955}})

        self.assertIsNone(plant.is_supported("plant_ess_soc"))

        data = await plant.async_update()

        self.assertIs(plant.is_supported("plant_ess_soh"), False)
        self.assertIs(plant.is_supported("plant_ess_soc"), True)
        self.assertIsNone(data["plant_ess_soh"])
        self.assertEqual(95.5, data["plant_ess_soc"])

    async def test_excluded_register_is_never_read_again(self) -> None:
        unit, plant = self._new_plant()
        soh_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soh"].address
        unit.fail_read(soh_addr, IllegalDataAddressError(2), register_type="input")
        await plant.async_update()

        unit.read_events.clear()
        await plant.async_update()

        read_addresses = {event.address for event in unit.read_events}
        self.assertNotIn(soh_addr, read_addresses)

    async def test_write_only_command_register_can_be_written(self) -> None:
        unit, plant = self._new_plant()
        await plant.async_write("plant_ess_max_charging_limit", 500)
        addr = self.defs.PLANT_PARAMETER_REGISTERS["plant_ess_max_charging_limit"].address
        self.assertIsNotNone(unit.holding.get(addr))

    async def test_write_to_confirmed_unsupported_register_raises(self) -> None:
        unit, plant = self._new_plant()
        soh_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soh"].address
        unit.fail_read(soh_addr, IllegalDataAddressError(2), register_type="input")
        await plant.async_update()

        with self.assertRaises(AttributeError):
            await plant.async_write("plant_ess_soh", 1)

    async def test_unsupported_write_is_blocked_while_sibling_probe_is_inconclusive(self) -> None:
        unit, plant = self._new_plant()
        unsupported = "plant_active_power_fixed_target"
        unresolved = "plant_reactive_power_fixed_target"
        unsupported_addr = self.defs.PLANT_PARAMETER_REGISTERS[unsupported].address
        unresolved_addr = self.defs.PLANT_PARAMETER_REGISTERS[unresolved].address
        unit.fail_read(unsupported_addr, IllegalDataAddressError(2), register_type="holding")
        unit.fail_read(unresolved_addr, ModbusTimeoutError("retry later"), register_type="holding")

        await plant.async_update()
        self.assertIs(plant.is_supported(unsupported), False)
        self.assertIsNone(plant.is_supported(unresolved))
        before = dict(unit.holding)

        with self.assertRaises(AttributeError):
            await plant.async_write(unsupported, 1000)

        self.assertEqual(before, unit.holding, "an unsupported register must never be written")

    async def test_supported_write_still_works_while_sibling_probe_is_inconclusive(self) -> None:
        unit, plant = self._new_plant()
        supported = "plant_active_power_fixed_target"
        unresolved = "plant_power_factor_target"
        # Illegal-address bulk responses permit immediate per-field probing;
        # the supported write must remain usable while another field times out.
        absent_addr = self.defs.PLANT_PARAMETER_REGISTERS["plant_reactive_power_fixed_target"].address
        unit.fail_read(absent_addr, IllegalDataAddressError(2), register_type="holding")
        unresolved_addr = self.defs.PLANT_PARAMETER_REGISTERS[unresolved].address
        unit.fail_read(unresolved_addr, ModbusTimeoutError("retry later"), register_type="holding")

        await plant.async_update()
        self.assertIs(plant.is_supported(supported), True)
        self.assertIsNone(plant.is_supported(unresolved))

        await plant.async_write(supported, 1000)
        field = plant.parameters.declared_fields[supported]
        actual = [unit.holding[field.address + offset] for offset in range(field.count)]
        self.assertEqual(field.encode(1000), actual)

    async def test_dead_link_propagates_as_connection_error(self) -> None:
        conn = MockModbusConnection()
        unit = conn.for_unit(1)
        unit.fail_requests(ModbusConnectionError())
        plant = self.devices.SigenergyPlant(unit)

        with self.assertRaises(ModbusConnectionError):
            await plant.async_update()

    async def _assert_cancelled_wire_request_is_quiet(self, stage: str) -> None:
        unit, plant = self._new_plant()
        if stage == "read":
            await plant.async_update()
        elif stage == "probe":
            first_field = next(iter(plant.running_info.declared_fields))
            plant._group._field_support["running_info"][first_field] = True

        loop = asyncio.get_running_loop()
        errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_handler)
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def delayed_failure(*args, **kwargs):
            started.set()
            await release.wait()
            finished.set()
            raise ModbusTimeoutError("late wire failure")

        method = "write_registers" if stage == "write" else "read_input_registers"
        with patch.object(unit, method, delayed_failure):
            operation = (
                plant.async_write("plant_active_power_fixed_target", 1000)
                if stage == "write" else plant.async_update()
            )
            caller = asyncio.create_task(operation)
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                caller.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await caller
            finally:
                release.set()
            await asyncio.wait_for(finished.wait(), timeout=1)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertEqual([], errors)

    async def test_cancelled_read_consumes_late_wire_failure(self) -> None:
        await self._assert_cancelled_wire_request_is_quiet("read")

    async def test_cancelled_probe_consumes_late_wire_failure(self) -> None:
        await self._assert_cancelled_wire_request_is_quiet("probe")

    async def _assert_cancelled_probe_backoff(self, *, timeout: bool = False) -> None:
        unit, plant = self._new_plant()
        flaky = "plant_ess_average_cell_temperature"
        reg = self.defs.PLANT_RUNNING_INFO_REGISTERS[flaky]
        soc_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soc"].address
        unit.load_raw({"input": {soc_addr: 955}})
        unit.fail_read(reg.address, ModbusTimeoutError("silent optional register"), register_type="input")
        original_read = unit.read_input_registers
        started, release = asyncio.Event(), asyncio.Event()
        probe_attempts = []

        async def slow_optional_read(address, count):
            if address == reg.address and count == reg.count:
                probe_attempts.append(address)
                started.set()
                await release.wait()
                raise ModbusTimeoutError("late optional-register timeout")
            if address <= reg.address < address + count:
                raise IllegalDataAddressError(2)  # enter individual probing this round
            return await original_read(address, count)

        with patch.object(unit, "read_input_registers", slow_optional_read):
            poll = asyncio.create_task(plant.async_update())
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                if timeout:
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(poll, timeout=0)
                else:
                    poll.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await poll
            finally:
                release.set()
                poll.cancel()
                await asyncio.gather(poll, *plant._group._pending_requests, return_exceptions=True)

            self.assertIs(plant.is_supported("plant_ess_soc"), True)
            self.assertIsNone(plant.is_supported(flaky))
            data = await plant.async_update()

        self.assertEqual(95.5, data["plant_ess_soc"])
        self.assertIsNone(data[flaky])
        self.assertEqual(1, len(probe_attempts), "the next poll must honour the probe retry delay")

        # Backoff is temporary: retry the unknown register and recover when
        # the optional read starts answering again. Never classify it as absent.
        unit.fail_read(reg.address, None, register_type="input")
        later = time.monotonic() + self.devices.PROBE_RETRY_DELAY + 1
        with patch.object(self.devices, "time", types.SimpleNamespace(monotonic=lambda: later)):
            recovered = await plant.async_update()
        self.assertIs(plant.is_supported(flaky), True)
        self.assertIsNotNone(recovered[flaky])

    async def test_cancelled_probe_backs_off_and_exposes_known_readings(self) -> None:
        await self._assert_cancelled_probe_backoff()

    async def test_timed_out_probe_backs_off_and_exposes_known_readings(self) -> None:
        await self._assert_cancelled_probe_backoff(timeout=True)

    async def test_cancelled_write_consumes_late_wire_failure(self) -> None:
        await self._assert_cancelled_wire_request_is_quiet("write")

    async def test_one_failing_block_does_not_blank_sibling_blocks(self) -> None:
        """A transient failure confined to one contiguous block must not
        blank fields whose block read successfully in the same cycle.
        """
        unit, plant = self._new_plant()
        soc_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soc"].address
        unit.load_raw({"input": {soc_addr: 955}})
        await plant.async_update()  # fully resolve first

        group = plant._group
        support = group._field_support["running_info"]
        component = group._readable["running_info"]
        known = {
            n: f for n, f in component.declared_fields.items()
            if support.get(n) and isinstance(f, self.devices.RegisterField)
        }
        blocks = self.devices._group_contiguous_blocks(
            [(f.address, f.count) for f in known.values()]
        )
        self.assertGreaterEqual(len(blocks), 2, "test needs at least 2 separate blocks")

        by_block: dict[int, list[str]] = {start: [] for start, _ in blocks}
        for field_name, field in known.items():
            for start, count in blocks:
                if start <= field.address < start + count:
                    by_block[start].append(field_name)
                    break
        block_a_start, block_b_start = blocks[0][0], blocks[1][0]
        field_in_a = by_block[block_a_start][0]
        field_in_b = by_block[block_b_start][0]

        unit.fail_read(block_b_start, ModbusTimeoutError("transient"), register_type="input")
        data = await plant.async_update()

        self.assertIsNotNone(data[field_in_a], "failure in block B must not blank block A")
        self.assertIsNone(data[field_in_b])

    async def test_known_fields_are_batched_not_read_one_by_one(self) -> None:
        """A persistently-unresolved field must not force one Modbus request
        per known-supported field on every poll.
        """
        unit, plant = self._new_plant()
        flaky_addr = self.defs.PLANT_RUNNING_INFO_REGISTERS[
            "plant_ess_average_cell_temperature"
        ].address
        unit.fail_read(flaky_addr, ModbusTimeoutError("flaky"), register_type="input")

        await plant.async_update()  # keep successful blocks; defer inconclusive ones
        self.assertIsNone(plant.is_supported("plant_ess_average_cell_temperature"))

        unit.read_events.clear()
        await plant.async_update()

        n_fields = len(self.defs.PLANT_RUNNING_INFO_REGISTERS)
        self.assertLess(
            len(unit.read_events),
            20,
            f"expected a handful of batched block reads for {n_fields - 1} known fields, "
            f"got {len(unit.read_events)} separate requests",
        )

    def _probe_group_with_optional_address(self, error, *, separate_block=True):
        from modbus_connection.model import Component, gauge

        conn = MockModbusConnection()
        unit = conn.for_unit(1)
        unit.load_raw({"input": {100: 123, 101: 0, 500: 456}})
        fields = {
            "register_space": "input", "working": gauge(100, 1.0), "optional": gauge(101, 1.0),
        }
        if separate_block:
            fields["separate"] = gauge(500, 1.0)
        component = type("ProbeFixture", (Component,), fields)(unit)
        group = self.devices._ComponentGroup({"telemetry": component}, {})
        original_read = unit.read_input_registers
        reads = []

        async def read(address, count):
            reads.append((address, count))
            if address <= 101 < address + count:
                raise error
            return await original_read(address, count)

        return unit, group, reads, read

    async def test_bulk_timeout_defers_fallback_and_keeps_other_blocks(self):
        unit, group, reads, read = self._probe_group_with_optional_address(
            ModbusTimeoutError("silent optional address")
        )
        with patch.object(unit, "read_input_registers", read):
            data = await group.async_update()

        self.assertEqual([(100, 2)], [(start, count) for start, count in reads if start < 500])
        self.assertEqual(456, data["separate"])
        self.assertIsNone(group.is_supported("working"))
        self.assertIsNone(group.is_supported("optional"))

    async def test_fallback_after_backoff_makes_progress_when_every_bulk_block_failed(self):
        unit, group, reads, read = self._probe_group_with_optional_address(
            ModbusTimeoutError("silent optional address"), separate_block=False,
        )
        with patch.object(unit, "read_input_registers", read):
            await group.async_update()
            self.assertIsNone(group.is_supported("working"))
            await group.async_update()
            self.assertEqual([(100, 2)], reads, "backoff must defer individual probes")

            later = time.monotonic() + self.devices.PROBE_RETRY_DELAY + 1
            with patch.object(self.devices, "time", types.SimpleNamespace(monotonic=lambda: later)):
                data = await group.async_update()

        self.assertEqual(123, data["working"])
        self.assertIs(group.is_supported("working"), True)
        self.assertIsNone(group.is_supported("optional"))
        self.assertEqual(1, reads.count((100, 2)), "do not repeat an inconclusive initial bulk pass forever")
        self.assertIn((100, 1), reads)
        self.assertIn((101, 1), reads)

    async def test_illegal_address_bulk_still_falls_back_in_the_same_poll(self):
        unit, group, reads, read = self._probe_group_with_optional_address(
            IllegalDataAddressError(2)
        )
        with patch.object(unit, "read_input_registers", read):
            data = await group.async_update()

        self.assertEqual(123, data["working"])
        self.assertIs(group.is_supported("working"), True)
        self.assertIs(group.is_supported("optional"), False)
        self.assertIn((100, 1), reads)
        self.assertIn((101, 1), reads)

    async def test_two_silent_optional_blocks_do_not_exhaust_first_refresh(self):
        from test_remote_ems_write_readback import _make_coordinator

        unit, plant = self._new_plant()
        input_address = self.defs.PLANT_RUNNING_INFO_REGISTERS[
            "plant_ess_average_cell_temperature"
        ].address
        holding_address = self.defs.PLANT_ESS_PREHEATING_REGISTERS[
            "plant_ess_preheating_enable"
        ].address
        soc_address = self.defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soc"].address
        unit.load_raw({"input": {soc_address: 955}})
        input_read, holding_read = unit.read_input_registers, unit.read_holding_registers
        slow_reads = []

        async def delayed_read(original, optional_address, space, address, count):
            if address <= optional_address < address + count:
                slow_reads.append((space, address, count))
                await asyncio.sleep(1)  # scaled equivalent of the 20s wire timeout
                raise ModbusTimeoutError("silent optional block")
            return await original(address, count)

        async def read_input(address, count):
            return await delayed_read(input_read, input_address, "input", address, count)

        async def read_holding(address, count):
            return await delayed_read(holding_read, holding_address, "holding", address, count)

        coordinator, hub, namespace = _make_coordinator()
        hub.async_read_plant_data = plant.async_update
        # Two wire budgets fit; retrying either silent address in this same
        # first refresh exceeds the deadline and would trigger a new setup.
        namespace["async_timeout"].timeout = lambda _seconds: asyncio.timeout(3)
        with patch.object(unit, "read_input_registers", read_input), patch.object(
            unit, "read_holding_registers", read_holding
        ):
            try:
                await coordinator.async_refresh()
            finally:
                await asyncio.gather(*plant._group._pending_requests, return_exceptions=True)

        self.assertTrue(
            coordinator.last_update_success,
            f"first refresh must preserve progress: {coordinator.last_exception!r}; reads={slow_reads!r}",
        )
        self.assertEqual(2, len(slow_reads), "each silent block should consume only one wire timeout")
        self.assertEqual(95.5, coordinator.data["plant"]["plant_ess_soc"])
        self.assertIsNone(plant.is_supported("plant_ess_average_cell_temperature"))
        self.assertIsNone(plant.is_supported("plant_ess_preheating_enable"))


@unittest.skipUnless(_HAVE_MODBUS_CONNECTION, "modbus-connection is not installed")
class TestSigenergyInverterAndDcCharger(unittest.IsolatedAsyncioTestCase):
    """The inverter and its optional DC charger poll and resolve independently."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.devices = _load_sigen_module("sigen_devices")
        cls.defs = _load_sigen_module("modbusregisterdefinitions")

    async def test_dc_charger_narrows_independently_of_the_inverter(self) -> None:
        conn = MockModbusConnection()
        unit = conn.for_unit(2)
        _load_zeroes(unit, "input", self.defs.INVERTER_RUNNING_INFO_REGISTERS)
        _load_zeroes(
            unit, "holding", self.defs.INVERTER_PARAMETER_REGISTERS, skip_write_only=True
        )
        dc_addr = self.defs.DC_CHARGER_RUNNING_INFO_REGISTERS[
            "dc_charger_charging_current"
        ].address
        unit.fail_read(dc_addr, IllegalDataAddressError(2), register_type="input")

        inverter = self.devices.SigenergyInverter(unit, has_dc_charger=True)

        inv_data = await inverter.async_update_inverter()
        self.assertGreater(len(inv_data), 0)

        dc_data = await inverter.async_update_dc_charger()
        self.assertIs(inverter.is_supported("dc_charger_charging_current"), False)
        self.assertIsNone(dc_data.get("dc_charger_charging_current"))
        self.assertIs(inverter.is_supported("inverter_active_power_fixed_adjustment"), True)


@unittest.skipUnless(_HAVE_MODBUS_CONNECTION, "modbus-connection is not installed")
class TestComponentGroupRecovery(unittest.IsolatedAsyncioTestCase):
    """Interrupted broker requests and malformed fields must stay isolated."""

    @classmethod
    def setUpClass(cls):
        cls.devices = _load_sigen_module("sigen_devices")

    def _new_group(self, unit, fields):
        from modbus_connection.model import Component

        component = type("RecoveryFixture", (Component,), {
            "register_space": "input", **fields,
        })(unit)
        return self.devices._ComponentGroup({"telemetry": component}, {})

    async def test_cancelled_bulk_probe_keeps_completed_blocks_and_resumes_unknown_only(self):
        from modbus_connection.model import gauge

        unit = MockModbusConnection().for_unit(1)
        unit.load_raw({"input": {100: 123, 500: 456}})
        group = self._new_group(unit, {
            "first": gauge(100, 1.0), "second": gauge(500, 1.0),
        })
        original_read = unit.read_input_registers
        blocked = asyncio.Event()
        release = asyncio.Event()
        reads = []

        async def read(address, count):
            reads.append((address, count))
            if address == 500 and not blocked.is_set():
                blocked.set()
                await release.wait()
            return await original_read(address, count)

        with patch.object(unit, "read_input_registers", read):
            poll = asyncio.create_task(group.async_update())
            try:
                await asyncio.wait_for(blocked.wait(), timeout=1)
                self.assertIs(group.is_supported("first"), True)
                self.assertIsNone(group.is_supported("second"))
                poll.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await poll
                self.assertIs(group.is_supported("first"), True)
                self.assertNotIn("telemetry", group._bulk_probed)
                release.set()
                await asyncio.gather(*group._pending_requests, return_exceptions=True)
                reads.clear()
                later = time.monotonic() + self.devices.PROBE_RETRY_DELAY + 1
                with patch.object(self.devices, "time", types.SimpleNamespace(monotonic=lambda: later)):
                    data = await group.async_update()
            finally:
                release.set()
                poll.cancel()
                await asyncio.gather(poll, *group._pending_requests, return_exceptions=True)

        self.assertEqual({"first": 123, "second": 456}, data)
        # Resume discovery at 500; the following 100/500 requests are normal
        # data reads. The already-confirmed first block is not probed again.
        self.assertEqual([(500, 1), (100, 1), (500, 1)], reads)

    async def _assert_survivors_are_drained(self, *, cancel_drain=False):
        from modbus_connection.model import gauge

        conn = MockModbusConnection()
        unit = conn.for_unit(1)
        unit.load_raw({"input": {100: 123, 500: 456}})
        groups = [
            self._new_group(unit, {"value": gauge(address, 1.0)})
            for address in (100, 500)
        ]
        for group in groups:
            group._field_support["telemetry"]["value"] = True

        broker_lock = asyncio.Lock()
        submitted, started, release = {}, {}, {}
        for address in (100, 500):
            submitted[address] = asyncio.Event()
            started[address] = asyncio.Event()
            release[address] = asyncio.Event()
        calls = []
        original_read = unit.read_input_registers

        async def broker_read(address, count):
            first_request = address not in calls
            calls.append(address)
            submitted[address].set()
            async with broker_lock:
                if first_request:
                    started[address].set()
                    await release[address].wait()
                    raise ModbusTimeoutError("late broker timeout")
                return await original_read(address, count)

        loop = asyncio.get_running_loop()
        errors = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_handler)
        polls = []
        with patch.object(unit, "read_input_registers", broker_read):
            try:
                polls = [asyncio.create_task(group.async_update()) for group in groups]
                for event in submitted.values():
                    await asyncio.wait_for(event.wait(), timeout=1)
                await asyncio.wait_for(started[100].wait(), timeout=1)
                self.assertFalse(started[500].is_set(), "second request must be queued")
                for poll in polls:
                    poll.cancel()
                await asyncio.gather(*polls, return_exceptions=True)
                survivors = [task for group in groups for task in group._pending_requests]
                self.assertEqual(2, len(survivors))

                polls = [asyncio.create_task(group.async_update()) for group in groups]
                # Let each poll reach its first suspension point: a safe poll
                # waits for its survivor instead of submitting another read.
                for _ in range(3):
                    await asyncio.sleep(0)
                self.assertEqual([100, 500], calls, "new polls must not grow the broker queue")

                if cancel_drain:
                    for poll in polls:
                        poll.cancel()
                    await asyncio.gather(*polls, return_exceptions=True)
                    self.assertTrue(all(not task.done() for task in survivors))
                    polls = [asyncio.create_task(group.async_update()) for group in groups]
                    for _ in range(3):
                        await asyncio.sleep(0)
                    self.assertEqual([100, 500], calls, "cancelling a drain must not bypass it")

                release[100].set()
                await asyncio.wait_for(started[500].wait(), timeout=1)
                for _ in range(3):
                    await asyncio.sleep(0)
                self.assertEqual(1, calls.count(500), "queued survivor must finish before replacement")
                release[500].set()
                data = await asyncio.wait_for(asyncio.gather(*polls), timeout=1)

                self.assertEqual([123, 456], [result["value"] for result in data])
                self.assertEqual([100, 500, 100, 500], calls)
                self.assertTrue(all(not task.cancelled() for task in survivors))
                self.assertTrue(all(not group._pending_requests for group in groups))
                self.assertEqual([], errors)
            finally:
                for event in release.values():
                    event.set()
                for poll in polls:
                    poll.cancel()
                await asyncio.gather(
                    *polls, *(task for group in groups for task in group._pending_requests),
                    return_exceptions=True,
                )

    async def test_cancelled_poll_drains_surviving_shared_broker_requests(self):
        await self._assert_survivors_are_drained()

    async def test_cancelled_drain_still_protects_requests_on_the_next_poll(self):
        await self._assert_survivors_are_drained(cancel_drain=True)

    async def test_short_field_does_not_hide_siblings_and_recovers(self):
        from modbus_connection.model import gauge

        for signed in (True, False):
            with self.subTest(signed=signed):
                conn = MockModbusConnection()
                unit = conn.for_unit(1)
                unit.load_raw({"input": {100: 123, 101: 789, 500: 456}})
                group = self._new_group(unit, {
                    "working": gauge(100, 1.0),
                    "truncated": gauge(101, 1.0, signed=signed),
                    "separate": gauge(500, 1.0),
                })
                original_read = unit.read_input_registers

                async def short_read(address, count):
                    words = await original_read(address, count)
                    return words[:1] if address == 100 else words

                with patch.object(unit, "read_input_registers", short_read):
                    data = await group.async_update()
                self.assertEqual(123, data["working"])
                self.assertEqual(456, data["separate"])
                self.assertIsNone(data["truncated"], "a missing word must not decode as zero")
                self.assertIs(group.is_supported("truncated"), True)

                recovered = await group.async_update()
                self.assertEqual(789, recovered["truncated"])

    async def test_failed_string_decoder_keeps_numeric_siblings_and_recovers(self):
        from modbus_connection.model import gauge, string

        conn = MockModbusConnection()
        unit = conn.for_unit(1)
        unit.load_raw({"input": {100: 123, 101: 0x4F4B, 102: 0, 103: 456}})
        group = self._new_group(unit, {
            "before": gauge(100, 1.0), "text": string(101, 2), "after": gauge(103, 1.0),
        })
        original_read = unit.read_input_registers

        async def malformed_read(address, count):
            words = await original_read(address, count)
            if address <= 101 < address + count:
                words[101 - address] = "sensitive malformed payload"
            return words

        with patch.object(unit, "read_input_registers", malformed_read):
            with self.assertLogs(self.devices._LOGGER, level="DEBUG") as captured:
                data = await group.async_update()
        self.assertEqual(123, data["before"])
        self.assertEqual(456, data["after"])
        self.assertIsNone(data["text"])
        self.assertIs(group.is_supported("text"), True)
        self.assertIn("text", " ".join(captured.output))
        self.assertNotIn("sensitive malformed payload", " ".join(captured.output))

        recovered = await group.async_update()
        self.assertEqual("OK", recovered["text"])


if __name__ == "__main__":
    unittest.main()
