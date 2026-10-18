"""Regression tests for modbus.py's hub: construction, connection sharing,
register-support dispatch, and the read-only write gate.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import unittest
from pathlib import Path

SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "sigen"
    / "modbus.py"
)

try:
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


class _ConfigEntry:
    def __init__(self, data: dict, entry_id: str = "test_entry") -> None:
        self.data = data
        self.entry_id = entry_id

    def async_on_unload(self, func) -> None:
        pass


#: `sigen.modbus` imports `async_get_unit` by name at module-import time, so
#: once it's imported, it keeps whichever function object was bound then --
#: re-registering a new stub in `homeassistant.components.modbus` on a later
#: test would have no effect on an already-imported `sigen.modbus`. Routing
#: through one persistent dict (cleared, not replaced, per test) keeps the
#: stub correct regardless of import caching across tests in this process.
_MOCK_CONNECTIONS: dict = {}


def _fake_async_get_unit(hass, entry, params, unit_id):
    key = (params.host, params.port)
    conn = _MOCK_CONNECTIONS.setdefault(key, MockModbusConnection())
    return conn.for_unit(unit_id)


def _install_stubs() -> None:
    """Register just enough of `homeassistant` for modbus.py to import and
    run, routing async_get_unit() to in-memory mocks keyed by (host, port) --
    the same connection-sharing semantics the real broker provides.
    """
    if "homeassistant.components.modbus" in sys.modules:
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

    ha_config_entries = types.ModuleType("homeassistant.config_entries")
    ha_config_entries.ConfigEntry = _ConfigEntry

    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object

    ha_exceptions = types.ModuleType("homeassistant.exceptions")
    ha_exceptions.HomeAssistantError = Exception

    ha_components = types.ModuleType("homeassistant.components")
    ha_modbus = types.ModuleType("homeassistant.components.modbus")
    ha_modbus.async_get_unit = _fake_async_get_unit

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.const"] = ha_const
    sys.modules["homeassistant.config_entries"] = ha_config_entries
    sys.modules["homeassistant.core"] = ha_core
    sys.modules["homeassistant.exceptions"] = ha_exceptions
    sys.modules["homeassistant.components"] = ha_components
    sys.modules["homeassistant.components.modbus"] = ha_modbus


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
class TestSigenergyModbusHub(unittest.IsolatedAsyncioTestCase):
    """The hub builds one shared connection per (host, port), and dispatches
    reads, writes and register-support lookups across plant/inverter/AC
    charger by name.
    """

    def setUp(self) -> None:
        _MOCK_CONNECTIONS.clear()
        self.mock_connections = _MOCK_CONNECTIONS
        self.modbus_mod = _load_sigen_module("modbus")
        self.const = _load_sigen_module("const")
        self.defs = _load_sigen_module("modbusregisterdefinitions")

    def _build_hub(self, *, read_only: bool = False):
        const = self.const
        entry_data = {
            const.CONF_PLANT_CONNECTION: {
                const.CONF_HOST: "10.0.0.5",
                const.CONF_PORT: 502,
                const.CONF_PLANT_ID: 247,
                const.CONF_SLAVE_ID: 247,
                const.CONF_READ_ONLY: read_only,
            },
            const.CONF_INVERTER_CONNECTIONS: {
                "Sigen Inverter": {
                    const.CONF_HOST: "10.0.0.5",
                    const.CONF_PORT: 502,
                    const.CONF_SLAVE_ID: 1,
                    const.CONF_INVERTER_HAS_DCCHARGER: False,
                }
            },
            const.CONF_AC_CHARGER_CONNECTIONS: {
                "Sigen AC Charger": {
                    const.CONF_HOST: "10.0.0.5",
                    const.CONF_PORT: 502,
                    const.CONF_SLAVE_ID: 5,
                }
            },
        }
        entry = _ConfigEntry(entry_data)
        hub = self.modbus_mod.SigenergyModbusHub(hass=None, config_entry=entry)

        conn = self.mock_connections[("10.0.0.5", 502)]
        plant_unit = conn.for_unit(247)
        _load_zeroes(plant_unit, "input", self.defs.PLANT_RUNNING_INFO_REGISTERS)
        _load_zeroes(
            plant_unit, "holding", self.defs.PLANT_PARAMETER_REGISTERS, skip_write_only=True
        )
        _load_zeroes(plant_unit, "holding", self.defs.PLANT_ESS_PREHEATING_REGISTERS)
        inv_unit = conn.for_unit(1)
        _load_zeroes(inv_unit, "input", self.defs.INVERTER_RUNNING_INFO_REGISTERS)
        _load_zeroes(
            inv_unit, "holding", self.defs.INVERTER_PARAMETER_REGISTERS, skip_write_only=True
        )
        ac_unit = conn.for_unit(5)
        _load_zeroes(ac_unit, "input", self.defs.AC_CHARGER_RUNNING_INFO_REGISTERS)
        _load_zeroes(
            ac_unit, "holding", self.defs.AC_CHARGER_PARAMETER_REGISTERS, skip_write_only=True
        )
        return hub, plant_unit

    async def test_plant_inverter_and_ac_charger_share_one_connection(self) -> None:
        self._build_hub()
        self.assertEqual(1, len(self.mock_connections))

    async def test_poll_diagnostics_are_dispatched_to_each_device_group(self) -> None:
        hub, _unit = self._build_hub()
        groups = {
            "plant": hub._plant._group,
            "inverter:Sigen Inverter": hub._inverters["Sigen Inverter"]._inverter_group,
            "ac_charger:Sigen AC Charger": hub._ac_chargers["Sigen AC Charger"]._group,
        }
        for index, (device, group) in enumerate(groups.items(), 1):
            group._poll_quality.update(status="partial", partial_update_count=index)
        for index, device in enumerate(groups, 1):
            quality = hub.get_poll_diagnostics(device)
            self.assertEqual(index, quality["partial_update_count"])
            self.assertEqual("partial", quality["status"])
        self.assertEqual({}, hub.get_poll_diagnostics("dc_charger:Sigen Inverter"))

    async def test_reads_across_all_device_types(self) -> None:
        hub, _ = self._build_hub()

        plant_data = await hub.async_read_plant_data()
        self.assertIn("plant_ems_work_mode", plant_data)

        inv_data = await hub.async_read_inverter_data("Sigen Inverter")
        self.assertGreater(len(inv_data), 0)

        dc_data = await hub.async_read_dc_charger_data("Sigen Inverter")
        self.assertEqual({}, dc_data, "this inverter has no DC charger")

        ac_data = await hub.async_read_ac_charger_data("Sigen AC Charger")
        self.assertGreater(len(ac_data), 0)

    async def test_get_register_support_dispatches_by_device_type(self) -> None:
        hub, _ = self._build_hub()
        await hub.async_read_plant_data()
        await hub.async_read_inverter_data("Sigen Inverter")
        await hub.async_read_ac_charger_data("Sigen AC Charger")

        self.assertIs(hub.get_register_support("plant", None, "plant_ems_work_mode"), True)
        self.assertIs(
            hub.get_register_support("inverter", "Sigen Inverter", "inverter_start_stop"), True
        )
        self.assertIs(
            hub.get_register_support(
                "ac_charger", "Sigen AC Charger", "ac_charger_system_state"
            ),
            True,
        )
        self.assertIsNone(
            hub.get_register_support("inverter", "Unknown Inverter", "inverter_start_stop")
        )

    async def test_write_parameter_across_device_types(self) -> None:
        hub, plant_unit = self._build_hub()

        await hub.async_write_parameter(
            "plant", None, "plant_active_power_fixed_target", 1000
        )
        addr = self.defs.PLANT_PARAMETER_REGISTERS["plant_active_power_fixed_target"].address
        self.assertIsNotNone(plant_unit.holding.get(addr))

        await hub.async_write_parameter(
            "inverter", "Sigen Inverter", "inverter_active_power_fixed_adjustment", 100
        )

        with self.assertRaises(self.modbus_mod.SigenergyModbusError):
            await hub.async_write_parameter(
                "inverter", "Nonexistent", "inverter_start_stop", 1
            )

    async def test_read_only_hub_blocks_writes(self) -> None:
        hub, plant_unit = self._build_hub(read_only=True)
        addr = self.defs.PLANT_PARAMETER_REGISTERS["plant_active_power_fixed_target"].address

        await hub.async_write_parameter(
            "plant", None, "plant_active_power_fixed_target", 999
        )

        self.assertNotEqual(999, plant_unit.holding.get(addr))


if __name__ == "__main__":
    unittest.main()
