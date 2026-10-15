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

        await plant.async_update()  # round 1: bulk fails, falls back to per-field probing
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


if __name__ == "__main__":
    unittest.main()
