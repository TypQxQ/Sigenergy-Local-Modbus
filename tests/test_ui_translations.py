"""Guard flow placeholders, additive alarm translations and error behavior."""
from __future__ import annotations

import __future__
import ast
import asyncio
from collections import Counter
import json
import re
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_alarm7_unknown_value import alarm, sensor_class

P = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"
LANGUAGES = {"da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "sv"}


def leaves(tree, prefix=()):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from leaves(value, (*prefix, key))
        else:
            yield (*prefix, key), value


class TestUICatalogues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.en = json.loads((P / "strings.json").read_text(encoding="utf-8"))
        cls.catalogues = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                          for path in (P / "translations").glob("*.json")}
        cls.sv = cls.catalogues["sv"]

    def test_all_ui_strings_have_all_supported_languages_and_matching_placeholders(self):
        english = dict(leaves(self.en))
        self.assertEqual(LANGUAGES, set(self.catalogues))
        self.assertEqual(self.en, self.catalogues["en"])
        placeholders = lambda text: Counter((name, spec, conversion)
            for _, name, spec, conversion in Formatter().parse(text) if name is not None)
        for language, catalogue in self.catalogues.items():
            translated_leaves = dict(leaves(catalogue))
            self.assertEqual(set(english), set(translated_leaves), language)
            for path, source in english.items():
                with self.subTest(language=language, path=path):
                    translated = translated_leaves[path]
                    self.assertIsInstance(translated, str)
                    self.assertTrue(translated.strip())
                    self.assertNotIn("\ufffd", translated)
                    self.assertEqual(placeholders(source), placeholders(translated))

    def test_new_languages_keep_numbers_and_electrical_channel_identifiers(self):
        english = dict(leaves(self.en))
        numbers = lambda text: re.findall(r"(?<!\w)-?\d+(?:\.\d+)?", text)
        for language in sorted(LANGUAGES - {"en", "sv"}):
            for path, translated in leaves(self.catalogues[language]):
                with self.subTest(language=language, path=path):
                    self.assertEqual(numbers(english[path]), numbers(translated))
            self.assertEqual(self.en["entity"]["sensor"]["inverter_output_type"]["state"],
                             self.catalogues[language]["entity"]["sensor"]["inverter_output_type"]["state"])

    def test_new_languages_localize_each_ui_section(self):
        for language in sorted(LANGUAGES - {"en", "sv"}):
            translated = dict(leaves(self.catalogues[language]))
            english = dict(leaves(self.en))
            paths = (("config", "step", "user", "title"),
                     ("options", "abort", "legacy_energy_adjustment_removed"),
                     ("entity", "sensor", "plant_battery_time_remaining", "name"),
                     ("entity", "sensor", "plant_running_state", "state", "running"),
                     ("entity", "sensor", "plant_general_alarm1", "state_attributes", "alarm_codes", "state", "bit_1"),
                     ("selector", "device_type", "options", "new_plant"),
                     ("exceptions", "invalid_pcc_power_factor", "message"),
                     ("services", "set_power_limit", "description"))
            for path in paths:
                with self.subTest(language=language, path=path):
                    self.assertNotEqual(english[path], translated[path])

    def test_catalogues_have_no_duplicate_json_keys(self):
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                self.assertNotIn(key, result, f"Duplicate JSON key: {key}")
                result[key] = value
            return result

        for path in (P / "strings.json", *(P / "translations").glob("*.json")):
            with self.subTest(catalogue=path.name):
                json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)

    def test_language_selector_and_configure_menu_cover_every_choice(self):
        for language, catalogue in self.catalogues.items():
            with self.subTest(language=language):
                self.assertEqual(LANGUAGES | {"auto"},
                                 set(catalogue["selector"]["entity_name_language"]["options"]))
                self.assertEqual({"select_device", "display_settings", "power_flow_settings"},
                                 set(catalogue["options"]["step"]["init"]["menu_options"]))
                self.assertIn("entity_name_language", catalogue["options"]["step"]["display_settings"]["data"])

    def test_battery_input_attributes_have_translated_labels_and_explicit_units(self):
        units = {"battery_soc_percent": "%", "backup_soc_percent": "%",
                 "rated_capacity_kwh": "kWh", "consumed_power_kw": "kW",
                 "usable_energy_kwh": "kWh"}
        for language, catalogue in self.catalogues.items():
            attributes = catalogue["entity"]["sensor"]["plant_battery_time_remaining"]["state_attributes"]
            self.assertEqual(set(units), set(attributes), language)
            for key, unit in units.items():
                self.assertIn(f"({unit})", attributes[key]["name"], (language, key))

    def test_flow_errors_and_abort_reasons_are_translation_keys(self):
        tree = ast.parse((P / "config_flow.py").read_text(encoding="utf-8"))
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            section = "options" if cls.name == "SigenergyOptionsFlowHandler" else "config"
            for node in ast.walk(cls):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                                and target.value.id == "errors"):
                            self.assertIsInstance(node.value, ast.Constant, ast.unparse(node))
                            self.assertIn(node.value.value, self.en[section]["error"])
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "async_abort"):
                    reason = next(kw.value for kw in node.keywords if kw.arg == "reason")
                    self.assertIsInstance(reason, ast.Constant)
                    self.assertIn(reason.value, self.en[section]["abort"])

    def test_generated_connection_forms_have_labels_for_their_actual_field_keys(self):
        # The config constant is plural (inverter_slave_ids). Checking only
        # catalogue parity misses a label filed under an unused singular key.
        constants = {"CONF_HOST": "host", "CONF_PORT": "port", "CONF_SLAVE_ID": "slave_id"}
        for node in ast.parse((P / "const.py").read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                try:
                    constants[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
        scope = {**constants, "_LOGGER": SimpleNamespace(debug=lambda *args: None),
                 "vol": SimpleNamespace(Required=lambda key, **kwargs: key,
                                        Schema=lambda data: data, All=lambda *args: None,
                                        Range=lambda **kwargs: None,
                                        Coerce=lambda value: value)}
        tree = ast.parse((P / "config_flow.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generate_plant_schema")
        exec(compile(ast.Module(body=[node], type_ignores=[]), "config_flow.py", "exec",
                     flags=__future__.annotations.compiler_flag), scope)
        values = {"host": "192.0.2.1", "port": 502, "slave_id": 247,
                  constants["CONF_INVERTER_SLAVE_ID"]: 1, "read_only": True, "scan_interval": 5}
        for new_installation, category in ((True, "config"), (False, "options")):
            fields = scope["generate_plant_schema"](values, use_inverter_device_id=new_installation,
                                                     display_update_frq=True)
            for catalogue in self.catalogues.values():
                self.assertLessEqual(set(fields), set(catalogue[category]["step"]["plant_config"]["data"]))

    def test_alarm_bits_and_reserved_bits_have_labels_in_each_language(self):
        for key in alarm.ALARM_REGISTER_GROUPS:
            if key not in self.en["entity"]["sensor"]:
                continue
            for doc in self.catalogues.values():
                attrs = doc["entity"]["sensor"][key]["state_attributes"]
                self.assertEqual({f"bit_{bit}" for bit in range(16)} | {"unknown"},
                                 set(attrs["alarm_codes"]["state"]))
                self.assertEqual({"no_problem", "active", "unknown"}, set(attrs["alarm_status"]["state"]))
        self.assertEqual("OVGR-fel", self.sv["entity"]["sensor"]["plant_general_alarm7"][
            "state_attributes"]["alarm_codes"]["state"]["bit_0"])

    def test_action_names_and_fields_match_existing_service_contracts(self):
        # Services remain identified by their original keys; labels don't
        # introduce any new actions or command values.
        self.assertEqual({"set_operation_mode", "set_remote_ems_control_mode", "set_power_limit"},
                         set(self.en["services"]))
        for key, item in self.en["services"].items():
            expected = {"value"} if key == "set_power_limit" else {"mode"}
            self.assertEqual(expected, set(item["fields"]))


class TestAlarmDetailBehavior(unittest.TestCase):
    def test_each_documented_bit_matches_the_existing_alarm_decoder(self):
        entity_type = sensor_class()
        groups = entity_type.native_value.fget.__globals__["ALARM_CODES"]
        catalogue = json.loads((P / "translations/en.json").read_text(encoding="utf-8"))["entity"]["sensor"]
        for key, group in alarm.ALARM_REGISTER_GROUPS.items():
            if key not in catalogue:
                continue
            for bit, expected in groups[group].items():
                with self.subTest(register=key, bit=bit):
                    entity = entity_type()
                    entity.entity_description = SimpleNamespace(key=key, device_class=None, value_fn=None)
                    entity._device_name = "test_device"
                    entity._round_digits = None
                    entity.base_attributes = None
                    if key.startswith("plant_"):
                        entity._device_type = "plant"
                        data = {"plant": {key: 1 << bit}}
                    else:
                        entity._device_type = "inverter" if key.startswith("inverter_") else "ac_charger"
                        section = "inverters" if entity._device_type == "inverter" else "ac_chargers"
                        data = {section: {"test_device": {key: 1 << bit}}}
                    entity.coordinator = SimpleNamespace(data=data)
                    self.assertEqual(expected, entity.native_value)
                    self.assertEqual([f"bit_{bit}"], entity.extra_state_attributes["alarm_codes"])
                    self.assertEqual(expected, catalogue[key]["state_attributes"]["alarm_codes"]["state"][f"bit_{bit}"])

    def test_combined_alarm_state_stays_english_but_each_detail_is_translatable(self):
        entity = sensor_class()()
        entity.entity_description = SimpleNamespace(key="plant_general_alarm1", device_class=None, value_fn=None)
        entity._device_type, entity._device_name = "plant", "plant"
        entity._round_digits = None
        entity.base_attributes = {"existing": "preserved"}
        entity.coordinator = SimpleNamespace(data={"plant": {"plant_general_alarm1": 3}})
        self.assertEqual("Software version mismatch, Low insulation resistance", entity.native_value)
        self.assertEqual({"existing": "preserved", "alarm_status": "active", "raw_value": 3,
                          "alarm_codes": ["bit_0", "bit_1"]}, entity.extra_state_attributes)
        sv = json.loads((P / "translations/sv.json").read_text(encoding="utf-8"))
        labels = sv["entity"]["sensor"]["plant_general_alarm1"]["state_attributes"]["alarm_codes"]["state"]
        self.assertEqual(["Programvaruversionerna stämmer inte överens", "Låg isolationsresistans"],
                         [labels[code] for code in entity.extra_state_attributes["alarm_codes"]])

    def test_reserved_bits_are_visible_and_missing_data_is_not_a_false_all_clear(self):
        mapping = {0: "Known alarm"}
        self.assertEqual({"alarm_status": "unknown", "alarm_codes": ["bit_6"], "raw_value": 64},
                         alarm.alarm_state_attributes("plant_general_alarm1", 64, mapping))
        self.assertEqual(["bit_0", "bit_6"], alarm.alarm_state_attributes("plant_general_alarm1", 65, mapping)["alarm_codes"])
        for missing in (None, "0", -1, 65536):
            self.assertIsNone(alarm.alarm_state_attributes("plant_general_alarm1", missing, mapping))


class TestTranslatedControlErrors(unittest.IsolatedAsyncioTestCase):
    async def test_missing_data_still_prevents_every_control_callback(self):
        cases = (("number.py", "SigenergyNumber", "async_set_native_value", (5,), "set_value_fn"),
                 ("select.py", "SigenergySelect", "async_select_option", ("Standby",), "select_option_fn"),
                 ("button.py", "SigenergyButton", "async_press", (), "press_fn"),
                 ("switch.py", "SigenergySwitch", "async_turn_on", (), "turn_on_fn"),
                 ("switch.py", "SigenergySwitch", "async_turn_off", (), "turn_off_fn"))
        class TranslatedError(Exception):
            def __init__(self, **kwargs):
                self.metadata = kwargs

        for source, cls, method, args, callback in cases:
            with self.subTest(source=source, method=method):
                tree = ast.parse((P / source).read_text(encoding="utf-8"))
                klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
                node = next(n for n in klass.body if isinstance(n, ast.AsyncFunctionDef) and n.name == method)
                namespace = {"HomeAssistantError": TranslatedError, "DOMAIN": "sigen"}
                exec(compile(ast.Module(body=[node], type_ignores=[]), source, "exec",
                             flags=__future__.annotations.compiler_flag), namespace)
                fn = AsyncMock()
                entity = SimpleNamespace(entity_id="test.original_entity", coordinator=SimpleNamespace(data=None),
                                         entity_description=SimpleNamespace(**{callback: fn}))
                with self.assertRaises(TranslatedError) as caught:
                    await namespace[method](entity, *args)
                self.assertEqual({"translation_domain": "sigen", "translation_key": "coordinator_unavailable",
                                  "translation_placeholders": {"entity_id": "test.original_entity"}}, caught.exception.metadata)
                fn.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
