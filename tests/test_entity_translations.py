"""Check naming coverage and identity without importing Home Assistant.

Evaluate the real description metadata, including every generated TOU slot,
and run the base entity constructor. Runtime name resolution is also checked
on the dev Home Assistant before these changes are submitted for review.
"""
from __future__ import annotations

import __future__
import ast
import json
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
import unittest


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"
SOURCES = {
    "binary_sensor.py": "binary_sensor", "button.py": "button",
    "calculated_sensor.py": "sensor", "static_sensor.py": "sensor",
    "number.py": "number", "select.py": "select", "switch.py": "switch",
}


def descriptions():
    """Read the identity/naming metadata of static and generated descriptions."""
    for source, domain in SOURCES.items():
        for node in ast.walk(ast.parse((COMPONENT / source).read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call)
                    and ast.unparse(node.func).endswith("EntityDescription")):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            if "key" not in keywords or "name" not in keywords:
                continue
            indices = range(1, 31) if isinstance(keywords["key"], ast.JoinedStr) else [None]
            for index in indices:
                metadata = {
                    name: eval(compile(ast.Expression(expr), source, "eval"), {"i": index})
                    for name, expr in keywords.items()
                    if name in {"key", "name", "translation_key", "translation_placeholders"}
                }
                yield domain, SimpleNamespace(**metadata)


def base_entity_class():
    """Run the production constructor and ID helpers with a coordinator stand-in."""
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    scope = {"CoordinatorEntity": CoordinatorEntity, "callback": lambda func: func}
    common = ast.parse((COMPONENT / "common.py").read_text(encoding="utf-8"))
    helpers = [node for node in common.body if isinstance(node, ast.FunctionDef)
               and node.name in {"generate_unique_entity_id", "generate_device_id"}]
    entity = ast.parse((COMPONENT / "sigen_entity.py").read_text(encoding="utf-8"))
    helpers += [node for node in entity.body if isinstance(node, ast.ClassDef)
                and node.name == "SigenergyEntity"]
    exec(compile(ast.Module(body=helpers, type_ignores=[]), "sigen_entity.py", "exec",
                 flags=__future__.annotations.compiler_flag), scope)
    return scope["SigenergyEntity"]


class TestEntityTranslations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogues = {
            path: json.loads((COMPONENT / path).read_text(encoding="utf-8"))["entity"]
            for path in ("strings.json", "translations/en.json", "translations/sv.json")
        }

    def test_every_entity_has_a_complete_name_in_both_languages(self):
        for domain, description in descriptions():
            key = getattr(description, "translation_key", None) or description.key
            placeholders = getattr(description, "translation_placeholders", {})
            for file, catalogue in self.catalogues.items():
                with self.subTest(file=file, domain=domain, entity=description.key):
                    name = catalogue[domain][key]["name"]
                    fields = {field for _, field, _, _ in Formatter().parse(name) if field}
                    self.assertEqual(fields, set(placeholders))
                    rendered = name.format(**placeholders)
                    self.assertTrue(rendered.strip())
                    self.assertNotIn("{", rendered)
                    self.assertNotIn("Limitation", rendered)

    def test_language_catalogues_have_the_same_keys_and_enum_states(self):
        english = self.catalogues["strings.json"]
        self.assertEqual(english, self.catalogues["translations/en.json"])
        swedish = self.catalogues["translations/sv.json"]
        self.assertEqual(set(english), set(swedish))
        for domain, entities in english.items():
            self.assertEqual(set(entities), set(swedish[domain]))
            for key, item in entities.items():
                states = item.get("state", {})
                translated = swedish[domain][key].get("state", {})
                self.assertEqual(set(states), set(translated), (domain, key))
                self.assertTrue(all(translated.values()))

    def test_power_limits_and_session_energy_are_unambiguous(self):
        english = self.catalogues["translations/en.json"]
        for key in ("plant_grid_point_maximum_export_limitation", "plant_grid_maximum_import_limitation",
                    "plant_pcs_maximum_export_limitation", "plant_pcs_maximum_import_limitation"):
            with self.subTest(key=key):
                self.assertIn("power limit", english["number"][key]["name"])
        self.assertEqual("Charging energy this session", english["sensor"][
            "dc_charger_current_charging_capacity"]["name"])

    def test_naming_does_not_change_unique_ids_or_override_ha_names(self):
        entity_class = base_entity_class()
        coordinator = SimpleNamespace(hub=SimpleNamespace(
            config_entry=SimpleNamespace(entry_id="existing-entry")))
        info = {"identifiers": {("sigen", "existing-device")}}
        for _, description in descriptions():
            with self.subTest(entity=description.key):
                entity = entity_class(coordinator, description, "Legacy name", "inverter",
                                      device_name="Inverter 1", device_info=info, pv_string_idx=3)
                self.assertEqual(f"existing-entry_inverter_1_pv3_{description.key}", entity._attr_unique_id)
                self.assertEqual(getattr(description, "translation_key", None) or description.key,
                                 entity._attr_translation_key)
                self.assertIs(description, entity.entity_description)
                self.assertIs(info, entity._attr_device_info)
                # _attr_name would take priority over HA translations. The entity
                # registry remains responsible for applying a user's custom name.
                self.assertFalse(hasattr(entity, "_attr_name"))

    def test_tou_periods_have_distinct_labels_and_unchanged_register_keys(self):
        periods = [desc for domain, desc in descriptions() if domain == "number"
                   and desc.key.startswith("plant_ess_preheating_tou_")]
        self.assertEqual(90, len(periods))
        for language in ("translations/en.json", "translations/sv.json"):
            names = [self.catalogues[language]["number"][desc.translation_key]["name"].format(
                **desc.translation_placeholders) for desc in periods]
            self.assertEqual(90, len(set(names)))
        for slot in (1, 30):
            self.assertIn(f"plant_ess_preheating_tou_{slot}_target_power", {d.key for d in periods})


if __name__ == "__main__":
    unittest.main()
