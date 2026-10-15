"""Regression tests for sigen_components.py's register-to-field conversion.

Confirms every modbus_connection Component built from the Sigenergy register
tables carries the exact address/gain/writability the source register
dictionaries declare, and that write-only registers are partitioned into a
separate, never-updated component (see sigen_components.py's module
docstring for why).
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
    / "sigen_components.py"
)

try:
    import modbus_connection  # noqa: F401
except ImportError:
    if os.environ.get("SIGEN_REQUIRE_MODBUS_CONNECTION") == "1":
        raise
    _HAVE_MODBUS_CONNECTION = False
else:
    _HAVE_MODBUS_CONNECTION = True


class _AnyAttr:
    """Stands in for a Home Assistant unit enum: any attribute access works."""

    def __getattr__(self, name: str) -> str:
        return f"stub:{name}"


def _install_stubs() -> None:
    """Register just enough of `homeassistant` for the sigen package to import.

    Avoids depending on a real Home Assistant install, which isn't declared
    as a test dependency for this repo.
    """
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
    """Import `sigen.<name>` from this repo's custom_components/sigen."""
    _install_stubs()
    if "sigen" not in sys.modules:
        package = types.ModuleType("sigen")
        package.__path__ = [str(SOURCE_PATH.parent)]
        sys.modules["sigen"] = package
    return importlib.import_module(f"sigen.{name}")


@unittest.skipUnless(_HAVE_MODBUS_CONNECTION, "modbus-connection is not installed")
class TestSigenComponents(unittest.TestCase):
    """Verify Component classes built from the register tables."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.comp = _load_sigen_module("sigen_components")
        cls.defs = _load_sigen_module("modbusregisterdefinitions")

    def _assert_partition(self, label, source_dict, readable_cls, write_only_cls=None):
        """`readable` + `write_only` fields must exactly match `source_dict`'s keys,
        with no overlap and nothing lost.
        """
        source_names = set(source_dict)
        readable_names = set(readable_cls.declared_fields)
        write_only_names = set(write_only_cls.declared_fields) if write_only_cls else set()

        with self.subTest(label=label):
            self.assertEqual(set(), readable_names & write_only_names)
            self.assertEqual(source_names, readable_names | write_only_names)

    def test_every_register_dict_is_fully_partitioned(self) -> None:
        comp, defs = self.comp, self.defs
        self._assert_partition(
            "plant running info", defs.PLANT_RUNNING_INFO_REGISTERS, comp.PlantRunningInfo
        )
        self._assert_partition(
            "plant parameters",
            defs.PLANT_PARAMETER_REGISTERS,
            comp.PlantParameters,
            comp.PlantCommands,
        )
        self._assert_partition(
            "plant preheating", defs.PLANT_ESS_PREHEATING_REGISTERS, comp.PlantPreheating
        )
        self._assert_partition(
            "inverter running info",
            defs.INVERTER_RUNNING_INFO_REGISTERS,
            comp.InverterRunningInfo,
        )
        self._assert_partition(
            "inverter parameters",
            defs.INVERTER_PARAMETER_REGISTERS,
            comp.InverterParameters,
            comp.InverterCommands,
        )
        self._assert_partition(
            "ac charger running info",
            defs.AC_CHARGER_RUNNING_INFO_REGISTERS,
            comp.ACChargerRunningInfo,
        )
        self._assert_partition(
            "ac charger parameters",
            defs.AC_CHARGER_PARAMETER_REGISTERS,
            comp.ACChargerParameters,
            comp.ACChargerCommands,
        )
        self._assert_partition(
            "dc charger running info",
            defs.DC_CHARGER_RUNNING_INFO_REGISTERS,
            comp.DCChargerRunningInfo,
        )
        self._assert_partition(
            "dc charger parameters",
            defs.DC_CHARGER_PARAMETER_REGISTERS,
            comp.DCChargerParameters,
            comp.DCChargerCommands,
        )

    def test_gain_becomes_the_reciprocal_scale(self) -> None:
        comp, defs = self.comp, self.defs
        reg = defs.PLANT_RUNNING_INFO_REGISTERS["plant_ess_soc"]
        field = comp.PlantRunningInfo.plant_ess_soc

        self.assertEqual(reg.address, field.address)
        self.assertEqual(1.0 / reg.gain, field.scale)
        self.assertFalse(field.signed)  # U16
        self.assertFalse(field.writable)  # READ_ONLY

    def test_multi_register_writable_field_keeps_its_word_count(self) -> None:
        comp, defs = self.comp, self.defs
        reg = defs.PLANT_PARAMETER_REGISTERS["plant_active_power_fixed_target"]
        field = comp.PlantParameters.plant_active_power_fixed_target

        self.assertEqual(reg.address, field.address)
        self.assertEqual(2, field.count)  # S32 -> two registers
        self.assertTrue(field.writable)  # HOLDING

    def test_write_only_register_lands_only_in_the_commands_component(self) -> None:
        comp, defs = self.comp, self.defs
        for name, reg in defs.PLANT_PARAMETER_REGISTERS.items():
            if reg.register_type is defs.RegisterType.WRITE_ONLY:
                with self.subTest(register=name):
                    self.assertIn(name, comp.PlantCommands.declared_fields)
                    self.assertNotIn(name, comp.PlantParameters.declared_fields)


if __name__ == "__main__":
    unittest.main()
