"""Behavioral tests for Home Assistant device hierarchy registration."""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path
from typing import Any


ROOT_PATH = Path(__file__).resolve().parents[1]
INTEGRATION_PATH = ROOT_PATH / "custom_components" / "sigen"
COMPAT_PATH = INTEGRATION_PATH / "device_registry_compat.py"
DOMAIN = "sigen"
ENTRY_ID = "test-entry"
PLANT_IDENTIFIER = (DOMAIN, f"{ENTRY_ID}_plant")
INVERTER_IDENTIFIER = (DOMAIN, f"{ENTRY_ID}_inverter_1")


def _load_compat_module():
    spec = importlib.util.spec_from_file_location("device_registry_compat", COMPAT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load device registry compatibility module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compat = _load_compat_module()


class _Device:
    def __init__(self, device_id: str, via_device_id: str | None) -> None:
        self.id = device_id
        self.via_device_id = via_device_id


class _RegistryBase:
    def __init__(self) -> None:
        self.devices: dict[tuple[str, str], _Device] = {}

    def _create(
        self,
        identifiers: set[tuple[str, str]],
        via_device_id: str | None,
    ) -> _Device:
        identifier = next(iter(identifiers))
        device = self.devices.get(identifier)
        if device is None:
            device = _Device(f"device-{len(self.devices) + 1}", via_device_id)
            self.devices[identifier] = device
        return device


class _LegacyRegistry(_RegistryBase):
    def async_get_or_create(
        self,
        *,
        identifiers: set[tuple[str, str]],
        via_device: tuple[str, str] | None = None,
        **kwargs: Any,
    ) -> _Device:
        if "via_device_id" in kwargs:
            raise TypeError("Legacy registry does not accept via_device_id")
        parent_id = self.devices[via_device].id if via_device is not None else None
        return self._create(identifiers, parent_id)


class _ModernRegistry(_RegistryBase):
    def async_get_or_create(
        self,
        *,
        identifiers: set[tuple[str, str]],
        via_device_id: str | None = None,
        **kwargs: Any,
    ) -> _Device:
        if "via_device" in kwargs:
            raise TypeError("Modern registry does not accept via_device")
        return self._create(identifiers, via_device_id)


class _LegacyRegistryApi:
    def __init__(self) -> None:
        self.registry = _LegacyRegistry()

    def async_get(self, hass: object) -> _LegacyRegistry:
        return self.registry


class _ModernRegistryApi:
    def __init__(self) -> None:
        self.registry = _ModernRegistry()

    def async_get(self, hass: object) -> _ModernRegistry:
        return self.registry

    def async_get_device_id_by_identifier(
        self,
        hass: object,
        identifier: tuple[str, str],
        *,
        config_entry_id: str,
    ) -> str:
        return self.registry.devices[identifier].id


class TestViaDeviceId(unittest.TestCase):
    """Ensure old and new Home Assistant versions preserve the hierarchy."""

    def _assert_hierarchy(self, registry_api: Any) -> None:
        hass = object()
        compat.register_parent_devices(
            hass,
            config_entry_id=ENTRY_ID,
            domain=DOMAIN,
            plant_name="Test Plant",
            inverter_connections={"Inverter 1": {}},
            coordinator_data={
                "inverters": {
                    "Inverter 1": {
                        "inverter_model_type": "Sigen Inverter",
                        "inverter_serial_number": "TEST123",
                    }
                }
            },
            device_id_fn=lambda name: name.lower().replace(" ", "_"),
            registry_api=registry_api,
        )

        registry = registry_api.registry
        plant = registry.devices[PLANT_IDENTIFIER]
        inverter = registry.devices[INVERTER_IDENTIFIER]
        self.assertEqual(plant.id, inverter.via_device_id)

        inverter_link = compat.parent_device_info(
            hass,
            ENTRY_ID,
            INVERTER_IDENTIFIER,
            registry_api=registry_api,
        )
        pv_string = registry.async_get_or_create(
            config_entry_id=ENTRY_ID,
            identifiers={(DOMAIN, f"{ENTRY_ID}_inverter_1_pv1")},
            **inverter_link,
        )
        dc_charger = registry.async_get_or_create(
            config_entry_id=ENTRY_ID,
            identifiers={(DOMAIN, f"{ENTRY_ID}_inverter_1_dc_charger")},
            **inverter_link,
        )
        self.assertEqual(inverter.id, pv_string.via_device_id)
        self.assertEqual(inverter.id, dc_charger.via_device_id)

        plant_link = compat.parent_device_info(
            hass,
            ENTRY_ID,
            PLANT_IDENTIFIER,
            registry_api=registry_api,
        )
        ac_charger = registry.async_get_or_create(
            config_entry_id=ENTRY_ID,
            identifiers={(DOMAIN, f"{ENTRY_ID}_ac_charger_1")},
            **plant_link,
        )
        self.assertEqual(plant.id, ac_charger.via_device_id)

    def test_legacy_device_registry_hierarchy(self) -> None:
        self._assert_hierarchy(_LegacyRegistryApi())

    def test_modern_device_registry_hierarchy(self) -> None:
        self._assert_hierarchy(_ModernRegistryApi())

    def test_deprecated_key_is_isolated_to_legacy_compatibility(self) -> None:
        matching_files: set[str] = set()
        for source_path in INTEGRATION_PATH.glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(
                    keyword.arg == "via_device" for keyword in node.keywords
                ):
                    matching_files.add(source_path.name)
                elif isinstance(node, ast.Dict) and any(
                    isinstance(key, ast.Constant) and key.value == "via_device"
                    for key in node.keys
                ):
                    matching_files.add(source_path.name)
        self.assertEqual({"device_registry_compat.py"}, matching_files)

    def test_parent_devices_are_registered_before_platform_setup(self) -> None:
        source_path = INTEGRATION_PATH / "__init__.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        setup = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_setup_entry"
        )
        register_call = next(
            node
            for node in ast.walk(setup)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register_parent_devices"
        )
        forward_call = next(
            node
            for node in ast.walk(setup)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_forward_entry_setups"
        )
        self.assertLess(register_call.lineno, forward_call.lineno)


if __name__ == "__main__":
    unittest.main()
