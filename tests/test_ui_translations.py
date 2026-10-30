"""Guard flow placeholders, additive alarm translations and error behavior."""
from __future__ import annotations

import __future__
import ast
import asyncio
import json
from pathlib import Path
from string import Formatter
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_alarm7_unknown_value import alarm, sensor_class

P = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"


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
        cls.sv = json.loads((P / "translations/sv.json").read_text(encoding="utf-8"))

    def test_all_ui_strings_have_swedish_and_matching_placeholders(self):
        english = dict(leaves(self.en))
        swedish = dict(leaves(self.sv))
        self.assertEqual(set(english), set(swedish))
        self.assertEqual(self.en, json.loads((P / "translations/en.json").read_text(encoding="utf-8")))
        for path, source in english.items():
            with self.subTest(path=path):
                translated = swedish[path]
                self.assertTrue(translated.strip())
                self.assertEqual({name for _, name, _, _ in Formatter().parse(source) if name},
                                 {name for _, name, _, _ in Formatter().parse(translated) if name})

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

    def test_alarm_bits_and_reserved_bits_have_labels_in_each_language(self):
        for key in alarm.ALARM_REGISTER_GROUPS:
            if key not in self.en["entity"]["sensor"]:
                continue
            for doc in (self.en, self.sv):
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
