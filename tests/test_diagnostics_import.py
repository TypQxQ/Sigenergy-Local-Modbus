"""Load the diagnostics module against Home Assistant's Modbus API shape."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SOURCE_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "sigen" / "diagnostics.py"
)


class TestDiagnosticsImport(unittest.TestCase):
    def test_loads_when_connection_info_exists_only_in_connection_module(self) -> None:
        # HA 2026.10 exports unit acquisition from modbus itself, but keeps
        # connection inspection in modbus.connection. Import the complete
        # diagnostics platform so an invalid top-level import really fails.
        package = "_sigen_diagnostics_import_test"
        names = (
            "homeassistant",
            "homeassistant.components",
            "homeassistant.components.diagnostics",
            "homeassistant.components.modbus",
            "homeassistant.components.modbus.connection",
            "homeassistant.config_entries",
            "homeassistant.const",
            "homeassistant.core",
            "homeassistant.helpers",
            "homeassistant.helpers.device_registry",
            package,
            f"{package}.const",
            f"{package}.modbusregisterdefinitions",
        )
        modules = {name: types.ModuleType(name) for name in names}
        for module in modules.values():
            module.__path__ = []
        helper = Mock(name="async_get_connection_info")
        modules["homeassistant.components.modbus.connection"].async_get_connection_info = helper
        modules["homeassistant.components.diagnostics"].async_redact_data = Mock()
        modules["homeassistant.config_entries"].ConfigEntry = object
        modules["homeassistant.core"].HomeAssistant = object
        modules["homeassistant.const"].__dict__.update(
            CONF_HOST="host", CONF_PASSWORD="password", CONF_USERNAME="username",
            __version__="2026.10.0",
        )
        modules["homeassistant.helpers.device_registry"].__dict__.update(
            DeviceEntry=object, async_entries_for_config_entry=Mock(), async_get=Mock()
        )
        modules[f"{package}.const"].__dict__.update(
            CONF_INVERTER_HAS_DCCHARGER="has_dc_charger", DOMAIN="sigen"
        )
        for device in ("AC_CHARGER", "DC_CHARGER", "INVERTER", "PLANT"):
            for kind in ("PARAMETER", "RUNNING_INFO"):
                setattr(
                    modules[f"{package}.modbusregisterdefinitions"],
                    f"{device}_{kind}_REGISTERS", {},
                )
        modules[f"{package}.modbusregisterdefinitions"].PLANT_ESS_PREHEATING_REGISTERS = {}

        spec = importlib.util.spec_from_file_location(f"{package}.diagnostics", SOURCE_PATH)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)

        self.assertIs(helper, module.async_get_connection_info)
        self.assertTrue(callable(module.async_get_config_entry_diagnostics))
        self.assertTrue(callable(module.async_get_device_diagnostics))


if __name__ == "__main__":
    unittest.main()
