"""Exercise real sensor value paths against the public 2.0 state contract."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_enum_sensor_states import COMPONENT_PATH, _load_register_definitions


# Explicit expectations protect external automation values from enum renames.
EXPECTED_STATES = {
    "plant_grid_sensor_status": {0: "offline", 1: "online"},
    "plant_on_off_grid_status": {0: "on_grid", 1: "off_grid_auto", 2: "off_grid_manual"},
    "plant_running_state": {
        0: "standby", 1: "running", 2: "fault", 3: "shutdown", 7: "environmental_abnormality",
    },
    "inverter_running_state": {
        0: "standby", 1: "running", 2: "fault", 3: "shutdown", 7: "environmental_abnormality",
    },
    "plant_ems_work_mode": {
        0: "max_self_consumption", 1: "ai_mode", 2: "tou", 5: "full_feed_in_to_grid",
        6: "vpp_scheduling", 7: "remote_ems", 9: "custom",
    },
    "inverter_output_type": {0: "l_n", 1: "l1_l2_l3", 2: "l1_l2_l3_n", 3: "l1_l2_n"},
    "ac_charger_system_state": {
        0: "initializing", 1: "not_connected", 2: "reserving", 3: "preparing",
        4: "ev_ready", 5: "charging", 6: "fault", 7: "error",
    },
    "dc_charger_running_state": {
        0: "idle", 1: "occupied", 2: "preparing_comm", 3: "charging", 4: "fault",
        5: "scheduled", 6: "ended", 7: "charger_unavailable", 8: "discharging",
        9: "alarm", 10: "preparing_insulation",
    },
}
MISSING = object()


def _descriptions(definitions):
    """Evaluate the actual eight description calls, including EMS's lambda."""
    namespace = {
        "SigenergySensorEntityDescription": lambda **kwargs: SimpleNamespace(
            **{"value_fn": None, "round_digits": None, **kwargs}),
        "SensorDeviceClass": SimpleNamespace(ENUM="enum"),
        "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"),
        "enum_sensor_options": definitions.enum_sensor_options,
        "ENUM_SENSOR_STATES": definitions.ENUM_SENSOR_STATES,
    }
    result = {}
    for filename in ("static_sensor.py", "calculated_sensor.py"):
        source = COMPONENT_PATH / filename
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "SigenergySensorEntityDescription"):
                continue
            key = next((kw.value for kw in node.keywords if kw.arg == "key"), None)
            if isinstance(key, ast.Constant) and key.value in EXPECTED_STATES:
                result[key.value] = eval(compile(ast.Expression(node), str(source), "eval"), namespace)
    return result


def _sensor_type(definitions, source_text=None):
    """Load production methods with HA-facing plumbing supplied by a stub."""
    class Base:
        @property
        def available(self):
            return self.coordinator.last_update_success

    source = COMPONENT_PATH / "sensor.py"
    tree = ast.parse(source_text or source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "SigenergySensor")
    methods = {"native_value", "_get_raw_value", "_decode_alarm_bits",
               "_apply_energy_zero_guard", "_is_near_daily_reset"}
    cls.body = [node for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name in methods]
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id in {
                     "_PROTECTED_DAILY_ENERGY_KEYS", "_PROTECTED_LIFETIME_ENERGY_KEYS",
                     "_DAILY_RESET_WINDOW",
                 } for target in node.targets)]
    namespace = {
        "SigenergyEntity": Base, "SensorEntity": type("SensorEntity", (), {}),
        "SensorDeviceClass": SimpleNamespace(TIMESTAMP="timestamp"),
        "Decimal": Decimal, "timedelta": timedelta,
        "dt_util": SimpleNamespace(now=lambda: datetime(2026, 10, 27, 12, tzinfo=timezone.utc)),
        "unknown_alarm_value": importlib.import_module("sigen.alarm").unknown_alarm_value,
        "ENUM_SENSOR_STATES": definitions.ENUM_SENSOR_STATES,
        "ALARM_CODES": definitions.ALARM_CODES,
        "RunningState": definitions.RunningState,
        "DCChargerRunningState": definitions.DCChargerRunningState,
        "DEVICE_TYPE_PLANT": "plant", "DEVICE_TYPE_INVERTER": "inverter",
        "DEVICE_TYPE_AC_CHARGER": "ac_charger", "DEVICE_TYPE_DC_CHARGER": "dc_charger",
        "_LOGGER": Mock(),
    }
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        *constants, cls,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    return namespace["SigenergySensor"]


class TestEnumSensorValues(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = _load_register_definitions()
        cls.descriptions = _descriptions(cls.definitions)
        cls.sensor_type = _sensor_type(cls.definitions)

    def sensor(self, key, raw=MISSING):
        entity = self.sensor_type()
        entity.entity_description = self.descriptions.get(key) or SimpleNamespace(
            key=key, value_fn=None, round_digits=None, device_class=None)
        entity.entity_id = "sensor.test_" + key
        entity._device_name = "Test device"
        entity._device_type = "plant" if key.startswith("plant_") else (
            "inverter" if key.startswith("inverter_") else (
                "ac_charger" if key.startswith("ac_charger_") else "dc_charger"))
        entity._round_digits = None
        entity._last_valid_daily_energy_value = None
        entity._last_valid_daily_energy_date = None
        values = {} if raw is MISSING else {key: raw}
        if entity._device_type == "plant":
            data = {"plant": values}
        else:
            data = {entity._device_type + "s": {entity._device_name: values}}
        entity.coordinator = SimpleNamespace(data=data, last_update_success=True)
        return entity, values

    def test_all_known_states_follow_the_external_contract(self):
        self.assertEqual(set(EXPECTED_STATES), set(self.definitions.ENUM_SENSOR_STATES))
        self.assertEqual(set(EXPECTED_STATES), set(self.descriptions))
        for key, states in EXPECTED_STATES.items():
            for raw, expected in states.items():
                with self.subTest(sensor=key, raw=raw):
                    entity, values = self.sensor(key, raw)
                    self.assertEqual(expected, entity.native_value)
                    self.assertIn(entity.native_value, entity.entity_description.options)
                    self.assertEqual(raw, values[key], "reading must not mutate coordinator data")

    def test_missing_and_unrecognized_values_are_unknown(self):
        for key in EXPECTED_STATES:
            for raw in (MISSING, None, -1, 65535):
                with self.subTest(sensor=key, raw=raw):
                    entity, _values = self.sensor(key, raw)
                    self.assertIsNone(entity.native_value)

    def test_absent_coordinator_data_is_unknown(self):
        for key in EXPECTED_STATES:
            with self.subTest(sensor=key):
                entity, _values = self.sensor(key, 0)
                entity.coordinator.data = None
                self.assertIsNone(entity.native_value)

    def test_known_unknown_known_sequence_uses_fresh_values(self):
        for key, states in EXPECTED_STATES.items():
            with self.subTest(sensor=key):
                first, last = min(states), max(states)
                entity, values = self.sensor(key, first)
                self.assertEqual(states[first], entity.native_value)
                values[key] = 65535
                self.assertIsNone(entity.native_value)
                values[key] = last
                self.assertEqual(states[last], entity.native_value)

    def test_ems_uses_its_real_value_function(self):
        entity, _values = self.sensor("plant_ems_work_mode", 7)
        fn = Mock(wraps=entity.entity_description.value_fn)
        entity.entity_description = SimpleNamespace(**{**vars(entity.entity_description), "value_fn": fn})
        self.assertEqual("remote_ems", entity.native_value)
        fn.assert_called_once_with(7, entity.coordinator.data, {"device_name": entity._device_name})

    def test_charger_status_does_not_override_transport_availability(self):
        entity, _values = self.sensor("dc_charger_running_state", 7)
        self.assertTrue(entity.available)
        self.assertEqual("charger_unavailable", entity.native_value)
        entity.coordinator.last_update_success = False
        self.assertFalse(entity.available)
        self.assertEqual("charger_unavailable", entity.native_value)

    def test_non_enum_numeric_readings_still_return_numbers(self):
        for value in (0, 1.25, -2.5):
            with self.subTest(value=value):
                entity, _values = self.sensor("inverter_active_power", value)
                self.assertEqual(value, entity.native_value)

    def test_daily_energy_zero_guard_is_preserved(self):
        entity, values = self.sensor("plant_daily_pv_energy", 4)
        self.assertEqual(4, entity.native_value)
        values["plant_daily_pv_energy"] = 0
        self.assertIsNone(entity.native_value)
        values["plant_daily_pv_energy"] = 4.25
        self.assertEqual(4.25, entity.native_value)


if __name__ == "__main__":
    unittest.main()
