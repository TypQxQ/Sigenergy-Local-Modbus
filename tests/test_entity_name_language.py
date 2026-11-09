"""Exercise language loading, entity preparation and real options-flow methods."""
from __future__ import annotations

import __future__
import ast
from copy import deepcopy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_config_options_safety import load_flows, options_flow, vol
from test_entity_translations import COMPONENT, descriptions


def language_scope():
    scope = load_flows()
    scope.update({"_LOGGER": logging.getLogger(__name__),
                  "async_get_translations": AsyncMock(return_value={"loaded": "name"})})
    tree = ast.parse((COMPONENT / "entity_language.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "entity_language.py", "exec",
                 flags=__future__.annotations.compiler_flag), scope)
    return scope


def entity_class(scope):
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        async def async_prepare_to_add_to_hass(self):
            self.prepared = True

        @property
        def suggested_object_id(self):
            # HA's default ID translation is independent of display language.
            return getattr(self, "_attr_name", self.entity_description.name)

        @property
        def translation_key(self):
            return self._attr_translation_key

        @property
        def translation_placeholders(self):
            return getattr(self.entity_description, "translation_placeholders", {})

    scope.update(CoordinatorEntity=CoordinatorEntity, callback=lambda f: f)
    common = ast.parse((COMPONENT / "common.py").read_text(encoding="utf-8"))
    nodes = [n for n in common.body if isinstance(n, ast.FunctionDef)
             and n.name in {"generate_unique_entity_id", "generate_device_id"}]
    tree = ast.parse((COMPONENT / "sigen_entity.py").read_text(encoding="utf-8"))
    nodes += [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SigenergyEntity"]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "sigen_entity.py", "exec",
                 flags=__future__.annotations.compiler_flag), scope)
    return scope["SigenergyEntity"]


def translations(language):
    catalogue = json.loads((COMPONENT / f"translations/{language}.json").read_text(encoding="utf-8"))
    return {f"component.sigen.entity.{domain}.{key}.name": value["name"]
            for domain, entities in catalogue["entity"].items() for key, value in entities.items()}


class TestNamePreparation(unittest.IsolatedAsyncioTestCase):
    async def test_all_languages_render_real_names_without_changing_identity_or_metadata(self):
        scope = language_scope()
        cls = entity_class(scope)
        for language in scope["ENTITY_NAME_LANGUAGES"]:
            mapping = translations(language)
            coordinator = SimpleNamespace(entity_name_translations=mapping,
                hub=SimpleNamespace(config_entry=SimpleNamespace(entry_id="existing-entry")))
            for domain, description in descriptions():
                with self.subTest(language=language, entity=description.key):
                    entity = cls(coordinator, description, "Legacy", "plant", device_name="Plant",
                                 device_info={"identifiers": {("sigen", "existing-device")}})
                    entity.platform_data = SimpleNamespace(domain=domain)
                    identity = entity._attr_unique_id
                    suggestion = entity.suggested_object_id
                    await entity.async_prepare_to_add_to_hass()
                    expected = mapping[f"component.sigen.entity.{domain}.{entity.translation_key}.name"]
                    self.assertEqual(expected.format(**entity.translation_placeholders), entity._attr_name)
                    self.assertEqual(suggestion, entity.suggested_object_id)
                    self.assertEqual(identity, entity._attr_unique_id)
                    self.assertIs(description, entity.entity_description)
                    self.assertTrue(entity.prepared)

    async def test_automatic_and_missing_names_leave_normal_ha_naming(self):
        for mapping in (None, {}, {"unrelated": "value"}):
            scope = language_scope()
            cls = entity_class(scope)
            domain, description = next(descriptions())
            coordinator = SimpleNamespace(entity_name_translations=mapping,
                hub=SimpleNamespace(config_entry=SimpleNamespace(entry_id="entry")))
            entity = cls(coordinator, description, "Legacy", "plant", device_info={"name": "Plant"})
            entity.platform_data = SimpleNamespace(domain=domain)
            suggestion = entity.suggested_object_id
            await entity.async_prepare_to_add_to_hass()
            self.assertFalse(hasattr(entity, "_attr_name"))
            self.assertEqual(suggestion, entity.suggested_object_id)

    async def test_loader_uses_selected_entry_language_once_and_automatic_skips_it(self):
        scope = language_scope()
        loader = scope["async_get_translations"]
        hass = object()
        entry = SimpleNamespace(options={})
        self.assertIsNone(await scope["async_load_entity_name_translations"](hass, entry))
        loader.assert_not_awaited()
        for language in scope["ENTITY_NAME_LANGUAGES"]:
            entry.options = {"entity_name_language": language}
            self.assertEqual({"loaded": "name"}, await scope["async_load_entity_name_translations"](hass, entry))
            loader.assert_awaited_with(hass, language, "entity", {"sigen"})
        loader.reset_mock()
        for invalid in ("xx", None, [], {"bad": "value"}):
            entry.options = {"entity_name_language": invalid}
            with self.assertLogs(__name__, level="WARNING"):
                self.assertIsNone(await scope["async_load_entity_name_translations"](hass, entry))
        loader.assert_not_awaited()

    def test_bad_translation_falls_back_instead_of_failing_entity_setup(self):
        scope = language_scope()
        for text in ("Slot {missing}", "Bad {slot"):
            with self.assertLogs(__name__, level="WARNING"):
                self.assertIsNone(scope["translated_entity_name"](
                    {"component.sigen.entity.number.test.name": text}, "number", "test", {}))


@unittest.skipUnless(vol is not None, "Install voluptuous to exercise the real form schema")
class TestLanguageOptions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = load_flows()
        self.scope.update(vol=vol, SelectSelectorConfig=lambda **kw: kw,
                          SelectSelector=lambda config: vol.In(config["options"]))
        self.flow = options_flow(self.scope)

    async def test_configure_menu_exposes_device_and_display_settings(self):
        result = await self.flow.async_step_init()
        self.assertEqual("menu", result["type"])
        self.assertEqual(["select_device", "display_settings", "power_flow_settings"], result["menu_options"])

    async def test_form_has_all_eleven_languages_and_automatic_with_current_default(self):
        for choice in ("auto", *self.scope["ENTITY_NAME_LANGUAGES"]):
            self.flow.config_entry.options = {"entity_name_language": choice}
            result = await self.flow.async_step_display_settings()
            self.assertEqual({"entity_name_language": choice}, result["data_schema"]({}))
            for language in ("auto", *self.scope["ENTITY_NAME_LANGUAGES"]):
                self.assertEqual({"entity_name_language": language},
                                 result["data_schema"]({"entity_name_language": language}))
            with self.assertRaises(vol.Invalid):
                result["data_schema"]({"entity_name_language": "xx"})

    async def test_save_only_returns_options_for_standard_persistence_and_one_listener_reload(self):
        for language in ("auto", *self.scope["ENTITY_NAME_LANGUAGES"]):
            flow = options_flow(self.scope)
            flow.config_entry.options = {"entity_name_language": "sv", "unrelated": {"keep": True}}
            before = deepcopy(flow.config_entry)
            result = await flow.async_step_display_settings({"entity_name_language": language})
            expected = {"unrelated": {"keep": True}}
            if language != "auto":
                expected["entity_name_language"] = language
            self.assertEqual({"type": "create_entry", "title": "", "data": expected}, result)
            self.assertEqual(before, flow.config_entry)
            self.assertIsNot(result["data"], flow.config_entry.options)
            flow.hass.config_entries.async_update_entry.assert_not_called()
            flow.hass.config_entries._async_schedule_save.assert_not_called()
            flow.hass.config_entries.async_reload.assert_not_awaited()

    async def test_invalid_input_and_saved_choice_do_not_mutate_or_reload(self):
        for invalid in ("xx", None, [], {}):
            flow = options_flow(self.scope)
            flow.config_entry.options = {"entity_name_language": invalid}
            before = deepcopy(flow.config_entry)
            form = await flow.async_step_display_settings()
            self.assertEqual({"entity_name_language": "auto"}, form["data_schema"]({}))
            result = await flow.async_step_display_settings({"entity_name_language": invalid})
            self.assertEqual({"entity_name_language": "invalid_entity_name_language"}, result["errors"])
            self.assertEqual(before, flow.config_entry)
            flow.async_create_entry.assert_not_called()
            flow.hass.config_entries.async_reload.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
