"""Exercise entity restoration callbacks without installing Home Assistant."""
from __future__ import annotations

import __future__
import ast
import asyncio
import logging
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"


class TestIncrementalEntities(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = False
        self.registry_entry = types.SimpleNamespace(
            unique_id="entry_plant_optional", entity_id="sensor.original_name",
            disabled_by="user", hidden_by=None)
        self.listeners, self.tasks, self.added = [], [], []
        self.registry = types.SimpleNamespace(async_update_entity=self.update_registry)
        self.hass = object()
        entry = types.SimpleNamespace(entry_id="entry", async_on_unload=lambda cb: None,
                                      async_create_task=self.create_task)
        hub = types.SimpleNamespace(incremental_discovery=True, config_entry=entry)
        hub.has_register = lambda *args: True
        self.coordinator = types.SimpleNamespace(
            hass=self.hass, hub=hub, last_update_success=True, data={"plant": {}},
            async_add_listener=self.add_listener)
        hider = types.SimpleNamespace(INTEGRATION=object())
        self.namespace = {
            "_LOGGER": logging.getLogger(__name__), "RegistryEntryHider": hider,
            "async_get_entity_registry": lambda hass: self.registry,
            "async_entries_for_config_entry": lambda reg, entry: [self.registry_entry],
            "get_entity_register_support": lambda *args: (self.state, ("optional",)),
            "get_source_entity_id": lambda *args: None,
            "DOMAIN": "sigen", "DEVICE_TYPE_PLANT": "plant",
            "DEVICE_TYPE_INVERTER": "inverter", "DEVICE_TYPE_DC_CHARGER": "dc_charger",
            "DEVICE_TYPE_AC_CHARGER": "ac_charger",
        }
        source = ast.parse((ROOT / "common.py").read_text(encoding="utf-8"))
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"generate_sigen_entity", "generate_unique_entity_id",
                                       "generate_device_id"}]
        exec(compile(ast.Module(body=functions, type_ignores=[]), "common.py", "exec",
                     flags=__future__.annotations.compiler_flag), self.namespace)
        self.generate = self.namespace["generate_sigen_entity"]
        platform = types.SimpleNamespace(async_add_entities=self.add_entities)
        module = types.ModuleType("homeassistant.helpers.entity_platform")
        self.platform_lookups = 0
        def current_platform():
            self.platform_lookups += 1
            return platform
        module.async_get_current_platform = current_platform
        self.patch = patch.dict(sys.modules, {"homeassistant.helpers.entity_platform": module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def update_registry(self, entity_id, **changes):
        self.assertEqual(self.registry_entry.entity_id, entity_id)
        for name, value in changes.items():
            setattr(self.registry_entry, name, value)

    def add_listener(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback) if callback in self.listeners else None

    def create_task(self, hass, coro, **kwargs):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    def add_entities(self, entities):
        self.added.extend(entities)

    def generate_optional(self, **overrides):
        description = types.SimpleNamespace(key="optional", name="Optional", **overrides)
        return self.generate("plant", "plant", None, self.coordinator,
                             lambda **kwargs: types.SimpleNamespace(**kwargs),
                             [description], "plant", self.hass)

    async def test_cached_negative_restores_without_changing_user_registry_flags(self):
        self.assertEqual([], self.generate_optional())
        self.assertIs(self.registry_entry.hidden_by, self.namespace["RegistryEntryHider"].INTEGRATION)
        self.state = True
        for listener in self.listeners[:]:
            listener()
        await asyncio.gather(*self.tasks)
        self.assertEqual(1, len(self.added))
        self.assertEqual("entry_plant_optional", self.namespace["generate_unique_entity_id"](
            "plant", "plant", self.coordinator, "optional"))
        self.assertIsNone(self.registry_entry.hidden_by)
        self.assertEqual("sensor.original_name", self.registry_entry.entity_id)
        self.assertEqual("user", self.registry_entry.disabled_by)
        self.assertEqual([], self.listeners)

    async def test_source_registration_can_finish_on_a_later_poll(self):
        self.assertEqual([], self.generate_optional(source_key="source"))
        self.state = True
        self.listeners[0]()
        self.assertEqual(1, self.platform_lookups, "retry must use the captured setup platform")
        self.assertEqual(1, len(self.listeners), "retry must not register duplicate listeners")
        self.namespace["get_source_entity_id"] = lambda *args: "sensor.source"
        self.listeners[0]()
        await asyncio.gather(*self.tasks)
        self.assertEqual("sensor.source", self.added[0].source_entity_id)

    async def test_unknown_optional_register_waits_for_evidence_before_becoming_an_entity(self):
        self.state = None
        self.assertEqual([], self.generate_optional())
        self.assertIsNone(self.registry_entry.hidden_by, "only confirmed negatives may be hidden")
        self.state = True
        self.listeners[0]()
        await asyncio.gather(*self.tasks)
        self.assertEqual(1, len(self.added))

    async def test_all_deferred_entities_restore_in_one_notification_without_tasks(self):
        descriptions = [types.SimpleNamespace(key=f"optional_{i}", name=f"Optional {i}")
                        for i in range(3)]
        self.assertEqual([], self.generate("plant", "plant", None, self.coordinator,
                         lambda **kwargs: types.SimpleNamespace(**kwargs), descriptions,
                         "plant", self.hass))
        self.state = True
        self.listeners[0]()
        self.assertEqual(3, len(self.added))
        self.assertEqual([], self.tasks, "HA's add-entities callback returns None")
        self.assertEqual([], self.listeners)

    async def test_synthetic_diagnostic_key_does_not_wait_for_discovery(self):
        self.state = None
        self.coordinator.hub.has_register = lambda *args: False
        self.assertEqual(1, len(self.generate_optional()))

    async def test_runtime_unsupported_entity_can_revive_and_diagnostics_stay_available(self):
        class Base:
            def _handle_coordinator_update(entity):
                entity.updates = getattr(entity, "updates", 0) + 1
        self.namespace.update(CoordinatorEntity=Base, callback=lambda fn: fn)
        source = ast.parse((ROOT / "sigen_entity.py").read_text(encoding="utf-8"))
        cls = next(node for node in source.body if isinstance(node, ast.ClassDef)
                   and node.name == "SigenergyEntity")
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name in {"available", "_handle_coordinator_update"}]
        exec(compile(ast.Module(body=[cls], type_ignores=[]), "sigen_entity.py", "exec",
                     flags=__future__.annotations.compiler_flag), self.namespace)
        entity = self.namespace["SigenergyEntity"]()
        entity.coordinator, entity.hub, entity.hass = self.coordinator, self.coordinator.hub, self.hass
        entity.entity_description = types.SimpleNamespace(key="optional")
        entity.registry_entry, entity.entity_id = self.registry_entry, self.registry_entry.entity_id
        entity._unsupported_removal_requested = False
        entity._device_type, entity._device_name, entity._pv_string_idx = "plant", "plant", None
        entity._handle_coordinator_update()
        self.assertFalse(entity.available)
        self.assertFalse(entity._unsupported_removal_requested)
        self.state = True
        entity._handle_coordinator_update()
        self.assertTrue(entity.available)
        self.assertIsNone(self.registry_entry.hidden_by)
        self.assertEqual("user", self.registry_entry.disabled_by)
        self.state = None
        self.assertFalse(entity.available, "implicit register dependencies must wait for support")
        self.coordinator.hub.has_register = lambda *args: False
        # Diagnostic/calculated keys need not be Modbus fields.
        self.assertTrue(entity.available)
        entity.entity_description.register_support_keys = ("voltage", "current")
        self.assertFalse(entity.available, "wait for declared calculation dependencies")
