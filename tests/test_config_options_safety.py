"""Exercise production flow methods with mocked persistence and device probes."""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_remote_ems_write_readback import _make_coordinator

try:
    import voluptuous as vol
except ModuleNotFoundError:
    vol = None

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/sigen"


def load_flows():
    scope = {"_LOGGER": Mock(), "CONF_NAME": "name", "generate_plant_schema": Mock(return_value="schema"),
             "get_highest_device_number": lambda names: 0}
    for node in ast.parse((SOURCE / "const.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                scope[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    tree = ast.parse((SOURCE / "config_flow.py").read_text(encoding="utf-8"))
    methods = {"_data", "_async_load_devices", "async_step_select_device",
               "async_step_accumulated_energy_config", "async_step_plant_config",
               "async_step_init", "async_step_display_settings",
               "async_step_inverter_config", "async_step_ac_charger_config"}
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
                "validate_host_port", "validate_slave_id", "device_address_in_use"}:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEFAULT_PLANT_CONNECTION" for t in node.targets):
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in {
                "SigenergyConfigFlow", "SigenergyOptionsFlowHandler"}:
            cls = deepcopy(node)
            cls.bases = []
            cls.keywords = []
            cls.decorator_list = []
            cls.body = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name in methods]
            nodes.append(cls)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
                 str(SOURCE / "config_flow.py"), "exec"), scope)
    return scope


def options_flow(scope):
    flow = scope["SigenergyOptionsFlowHandler"]()
    flow.config_entry = SimpleNamespace(entry_id="fixture", options={}, data={
        "device_type": "plant", "name": "Fixture plant",
        "plant_connection": {"host": "192.0.2.1", "port": 502, "slave_id": 247,
                             "read_only": True, "scan_interval": 5},
        "inverter_connections": {"Fixture inverter": {"slave_id": 1, "inverter_has_dccharger": True}},
        "ac_charger_connections": {"Fixture charger": {"slave_id": 2}},
        "values_to_initialize": {"sensor.legacy_pending": 1234567.0},
    })
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=Mock(), _async_schedule_save=Mock(), async_reload=AsyncMock()))
    flow.async_abort = Mock(side_effect=lambda **kw: {"type": "abort", **kw})
    flow.async_show_form = Mock(side_effect=lambda **kw: {"type": "form", **kw})
    flow.async_show_menu = Mock(side_effect=lambda **kw: {"type": "menu", **kw})
    flow.async_create_entry = Mock(side_effect=lambda **kw: {"type": "create_entry", **kw})
    flow._temp_config = {}
    return flow


def plant_input(interval):
    return {"host": "192.0.2.1", "port": 502, "slave_id": 247,
            "inverter_slave_ids": 1, "read_only": True, "scan_interval": interval}


@unittest.skipUnless(vol is not None, "Install voluptuous to exercise the real form schema")
class TestPlantSchema(unittest.TestCase):
    def setUp(self):
        scope = load_flows()
        scope["vol"] = vol
        tree = ast.parse((SOURCE / "config_flow.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "generate_plant_schema")
        module = ast.Module(body=[ast.ImportFrom(module="__future__",
            names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(SOURCE / "config_flow.py"), "exec"), scope)
        self.generate_schema = scope["generate_plant_schema"]

    def schema_and_input(self, initial, interval):
        data = plant_input(interval)
        data.pop("slave_id" if initial else "inverter_slave_ids")
        schema = self.generate_schema(data, use_inverter_device_id=initial, display_update_frq=True)
        return schema, data

    def test_real_setup_and_options_schema_accept_and_coerce_supported_intervals(self):
        for initial in (True, False):
            for interval in (1, 2, 5, "1", "5"):
                with self.subTest(initial=initial, interval=interval):
                    schema, data = self.schema_and_input(initial, interval)
                    result = schema(data)
                    self.assertEqual(int(interval), result["scan_interval"])
                    self.assertIsInstance(result["scan_interval"], int)

    def test_real_setup_and_options_schema_reject_intervals_below_one(self):
        for initial in (True, False):
            for interval in (0, -1, "0", "-1"):
                with self.subTest(initial=initial, interval=interval):
                    schema, data = self.schema_and_input(initial, interval)
                    with self.assertRaises(vol.Invalid):
                        schema(data)


class TestPollingInterval(unittest.IsolatedAsyncioTestCase):
    async def test_options_saved_interval_is_used_by_coordinator(self):
        for interval in (1, 2, 5):
            with self.subTest(interval=interval):
                scope = load_flows()
                flow = options_flow(scope)
                flow.config_entry.options = {"entity_name_language": "sv", "other": True}
                result = await flow.async_step_plant_config(plant_input(interval))
                self.assertEqual("create_entry", result["type"])
                self.assertEqual(flow.config_entry.options, result["data"])
                saved = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
                stored_interval = saved["plant_connection"]["scan_interval"]
                _, hub, ns = _make_coordinator()
                ns.update({key: scope[key] for key in ("MIN_SCAN_INTERVAL", "DEFAULT_SCAN_INTERVAL")})
                coordinator = ns["SigenergyDataUpdateCoordinator"](
                    object(), Mock(), hub, "test", stored_interval)
                self.assertEqual(timedelta(seconds=interval), coordinator.update_interval)
                flow.hass.config_entries.async_reload.assert_awaited_once_with("fixture")
                self.assertEqual({"sensor.legacy_pending": 1234567.0}, saved["values_to_initialize"])

    async def test_options_invalid_interval_does_not_persist_or_reload(self):
        for interval in (0, -1):
            with self.subTest(interval=interval):
                flow = options_flow(load_flows())
                before = deepcopy(flow.config_entry.data)
                result = await flow.async_step_plant_config(plant_input(interval))
                self.assertEqual({"scan_interval": "must_be_at_least_1"}, result["errors"])
                self.assertEqual(before, flow.config_entry.data)
                flow.hass.config_entries.async_update_entry.assert_not_called()
                flow.hass.config_entries._async_schedule_save.assert_not_called()
                flow.hass.config_entries.async_reload.assert_not_awaited()

    async def test_new_install_invalid_interval_never_probes_or_creates_entry(self):
        for interval in (0, -1):
            with self.subTest(interval=interval):
                scope = load_flows()
                flow = scope["SigenergyConfigFlow"]()
                flow._discovered_ip = None
                flow._data = {}
                flow.async_check_device_type = AsyncMock()
                flow.async_show_form = Mock(side_effect=lambda **kw: {"type": "form", **kw})
                flow.async_create_entry = Mock()
                result = await flow.async_step_plant_config(plant_input(interval))
                self.assertEqual({"scan_interval": "must_be_at_least_1"}, result["errors"])
                flow.async_check_device_type.assert_not_awaited()
                flow.async_create_entry.assert_not_called()
                self.assertEqual({}, flow._data)

    async def test_new_install_preserves_valid_one_second_interval(self):
        scope = load_flows()
        flow = scope["SigenergyConfigFlow"]()
        flow._data, flow._plants = {}, {}
        flow.async_check_device_type = AsyncMock(return_value=scope["DEVICE_TYPE_INVERTER"])
        flow.async_create_entry = Mock(side_effect=lambda **kw: kw)
        result = await flow.async_step_plant_config(plant_input(1))
        self.assertEqual(1, result["data"]["plant_connection"]["scan_interval"])
        flow.async_check_device_type.assert_awaited_once()

    async def test_invalid_saved_interval_falls_back_without_rewriting_configuration(self):
        scope = load_flows()
        _, hub, ns = _make_coordinator()
        ns.update({key: scope[key] for key in ("MIN_SCAN_INTERVAL", "DEFAULT_SCAN_INTERVAL")})
        for interval in (0, -1):
            with self.subTest(interval=interval):
                logger = Mock()
                coordinator = ns["SigenergyDataUpdateCoordinator"](object(), logger, hub, "test", interval)
                self.assertEqual(timedelta(seconds=5), coordinator.update_interval)
                logger.warning.assert_called_once()


class TestRetiredEnergyAdjustment(unittest.IsolatedAsyncioTestCase):
    async def test_menu_keeps_existing_devices_and_omits_legacy_energy_choice(self):
        flow = options_flow(load_flows())
        await flow._async_load_devices()
        self.assertEqual({"plant_fixture", "inverter_Fixture inverter", "ac_Fixture charger",
                          "dc_Fixture inverter"}, set(flow._devices))

    async def test_empty_and_numeric_stale_submissions_abort_without_mutation(self):
        scope = load_flows()
        values = {entity_id: 12.0 for entity_id in scope["RESETTABLE_SENSORS"]}
        for submission in (None, {}, values):
            with self.subTest(submission=submission):
                flow = options_flow(scope)
                before = deepcopy(flow.config_entry.data)
                result = await flow.async_step_accumulated_energy_config(submission)
                self.assertEqual({"type": "abort", "reason": "legacy_energy_adjustment_removed"}, result)
                self.assertEqual(before, flow.config_entry.data)
                flow.hass.config_entries.async_update_entry.assert_not_called()
                flow.hass.config_entries._async_schedule_save.assert_not_called()
                flow.hass.config_entries.async_reload.assert_not_awaited()
                flow.async_create_entry.assert_not_called()

    async def test_stale_menu_selection_routes_to_guard(self):
        flow = options_flow(load_flows())
        result = await flow.async_step_select_device({"selected_device": "accumulated_energy"})
        self.assertEqual({"type": "abort", "reason": "legacy_energy_adjustment_removed"}, result)
        flow.hass.config_entries.async_update_entry.assert_not_called()
        flow.hass.config_entries.async_reload.assert_not_awaited()

    async def test_current_device_options_still_route_to_existing_steps(self):
        for choice, method in (("plant_fixture", "async_step_plant_config"),
                               ("inverter_Fixture inverter", "async_step_inverter_config"),
                               ("ac_Fixture charger", "async_step_ac_charger_config"),
                               ("dc_Fixture inverter", "async_step_dc_charger_config")):
            with self.subTest(choice=choice):
                flow = options_flow(load_flows())
                target = AsyncMock(return_value="next step")
                setattr(flow, method, target)
                self.assertEqual("next step", await flow.async_step_select_device({"selected_device": choice}))
                target.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
