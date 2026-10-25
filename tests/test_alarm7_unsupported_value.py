"""Exercise the sensor's real value/availability paths for Alarm 7's sentinel."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1] / "custom_components/sigen"
spec = importlib.util.spec_from_file_location("alarm_helpers", ROOT / "alarm.py")
alarm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alarm)


def sensor_class():
    """Run production sensor methods with only HA's base entity stubbed."""
    class Base:
        @property
        def available(self):
            return self.base_available

        @property
        def extra_state_attributes(self):
            return self.base_attributes

        def _apply_energy_zero_guard(self, value):
            return value

    tree = ast.parse((ROOT / "sensor.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SigenergySensor")
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {
        "available", "extra_state_attributes", "native_value", "_get_raw_value", "_decode_alarm_bits",
    }]
    definitions = ast.parse((ROOT / "modbusregisterdefinitions.py").read_text(encoding="utf-8"))
    mappings = next(n.value for n in definitions.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "ALARM_CODES" for t in n.targets))
    namespace = {
        "SigenergyEntity": Base, "SensorEntity": type("SensorEntity", (), {}),
        "unsupported_alarm_value": alarm.unsupported_alarm_value,
        "SensorDeviceClass": SimpleNamespace(TIMESTAMP="timestamp"),
        "ALARM_CODES": ast.literal_eval(mappings),
        "DEVICE_TYPE_PLANT": "plant", "DEVICE_TYPE_INVERTER": "inverter",
        "DEVICE_TYPE_AC_CHARGER": "ac_charger", "DEVICE_TYPE_DC_CHARGER": "dc_charger",
    }
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / "sensor.py"), "exec"), namespace)
    return namespace["SigenergySensor"]


class TestAlarm7UnsupportedValue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sensor_type = sensor_class()

    def sensor(self, value, key="plant_general_alarm7"):
        entity = self.sensor_type()
        entity.entity_description = SimpleNamespace(key=key, device_class=None, value_fn=None)
        entity._device_type, entity._device_name = "plant", "plant"
        entity._round_digits = None
        entity.base_available, entity.base_attributes = True, None
        entity.coordinator = SimpleNamespace(data={"plant": {key: value}})
        return entity

    def test_all_bits_set_is_unavailable_and_never_decodes_as_faults(self):
        entity = self.sensor(0xFFFF)
        self.assertFalse(entity.available)
        self.assertIsNone(entity.native_value)
        self.assertEqual({"register_status": "unsupported", "unsupported_reason": "all_bits_set",
                          "raw_value": 65535}, entity.extra_state_attributes)
        self.assertEqual(65535, entity.coordinator.data["plant"]["plant_general_alarm7"])

    def test_each_documented_bitmap_still_reports_its_real_alarm(self):
        for value, expected in ((0, "No Problem"), (1, "OVGR fault"), (2, "RPR fault"),
                                (3, "OVGR fault, RPR fault")):
            with self.subTest(value=value):
                entity = self.sensor(value)
                self.assertTrue(entity.available)
                self.assertEqual(expected, entity.native_value)
                self.assertIsNone(entity.extra_state_attributes)

    def test_missing_data_is_unknown_instead_of_healthy_or_unsupported(self):
        entity = self.sensor(None)
        self.assertIsNone(entity.native_value)
        self.assertIsNone(entity.extra_state_attributes)

    def test_same_entity_recovers_on_next_usable_read_and_can_degrade_again(self):
        entity = self.sensor(0xFFFF)
        for value in (0, 1, 2, 3):
            with self.subTest(value=value):
                entity.coordinator.data["plant"]["plant_general_alarm7"] = 0xFFFF
                self.assertFalse(entity.available)
                entity.coordinator.data["plant"]["plant_general_alarm7"] = value
                self.assertTrue(entity.available)
                self.assertIsNotNone(entity.native_value)
                self.assertIsNone(entity.extra_state_attributes)

    def test_other_alarm_registers_keep_all_bits_set(self):
        for key in ("plant_general_alarm1", "plant_general_alarm2", "plant_general_alarm6"):
            with self.subTest(key=key):
                entity = self.sensor(0xFFFF, key)
                self.assertTrue(entity.available)
                self.assertIsNotNone(entity.native_value)
                self.assertIsNone(entity.extra_state_attributes)

    def test_parent_unavailability_and_existing_attributes_are_preserved(self):
        entity = self.sensor(0)
        entity.base_available = False
        self.assertFalse(entity.available)
        entity.base_attributes = {"existing": "preserved"}
        entity.coordinator.data["plant"]["plant_general_alarm7"] = 0xFFFF
        self.assertEqual("preserved", entity.extra_state_attributes["existing"])
        self.assertEqual({"existing": "preserved"}, entity.base_attributes)
        entity.coordinator.data = None
        self.assertIsNone(entity.native_value)
        self.assertEqual(entity.base_attributes, entity.extra_state_attributes)

    def test_diagnostics_explain_only_the_current_alarm7_sentinel(self):
        data = {"plant": {"plant_general_alarm7": 0xFFFF, "plant_general_alarm1": 0xFFFF}}
        result = alarm.unsupported_alarm_diagnostics(data)
        self.assertEqual({"plant_general_alarm7"}, set(result))
        self.assertEqual("unsupported", result["plant_general_alarm7"]["status"])
        self.assertTrue(result["plant_general_alarm7"]["continues_polling"])
        result["plant_general_alarm7"]["raw_value"] = 0
        self.assertEqual(0xFFFF, data["plant"]["plant_general_alarm7"])
        for value in (None, 0, 1, 2, 3):
            data["plant"]["plant_general_alarm7"] = value
            self.assertEqual({}, alarm.unsupported_alarm_diagnostics(data))
        self.assertEqual({}, alarm.unsupported_alarm_diagnostics(None))


if __name__ == "__main__":
    unittest.main()
