"""Downloaded entry/device diagnostics expose restore health without extra I/O."""
from __future__ import annotations

import ast
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_remote_ems_write_readback import _make_coordinator
from test_pv_restore import pv, NOW


class TestRestoreDiagnostics(unittest.IsolatedAsyncioTestCase):
    async def test_both_downloads_include_pv_restore_and_partial_update_counter(self):
        source = Path(__file__).resolve().parents[1] / "custom_components/sigen/diagnostics.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        namespace = {
            "__builtins__": __builtins__, "DOMAIN": "sigen", "TO_REDACT": set(),
            "async_redact_data": lambda value, _keys: value,
            "async_get_device_registry": Mock(return_value=object()),
            "async_entries_for_config_entry": Mock(return_value=[]),
            "async_get_connection_info": Mock(return_value=[]),
            "CONF_INVERTER_HAS_DCCHARGER": "has_dc_charger",
            "HA_VERSION": "2026.10.2", "pymodbus_version": "test", "tmodbus_version": "test",
        }
        for name in ("AC_CHARGER_PARAMETER_REGISTERS", "AC_CHARGER_RUNNING_INFO_REGISTERS",
                     "DC_CHARGER_PARAMETER_REGISTERS", "DC_CHARGER_RUNNING_INFO_REGISTERS",
                     "INVERTER_PARAMETER_REGISTERS", "INVERTER_RUNNING_INFO_REGISTERS",
                     "PLANT_ESS_PREHEATING_REGISTERS", "PLANT_PARAMETER_REGISTERS",
                     "PLANT_RUNNING_INFO_REGISTERS"):
            namespace[name] = {}
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *functions,
        ], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
        coordinator, _hub, _ns = _make_coordinator()
        coordinator.partial_update_count = 7
        coordinator.update_interval = timedelta(seconds=5)
        coordinator.data = {}
        coordinator.pv_restore_states["sensor.pv"] = {
            "status": "restored", "source": "snapshot",
            "native_value_kwh": "6333.170261123", "sample_updated": "2026-10-18T12:00:00+00:00",
            "attempts": 2, "recorder_queue_wait": 8, "recorder_query_time": 4,
            "last_error": {"type": "TimeoutError", "stage": "recorder"},
            "total_elapsed": 12, "_started_at": None, "_job_started_at": None,
        }
        rejection = {"reasons": ["unavailable_state"], "latest_updated": NOW.isoformat(),
                     "cutoff": NOW.isoformat()}
        job = pv.RecorderJobProgress(100)
        job.started(108)
        job.rejected_latest(rejection)
        job.finished(112)
        failed_job = pv.RecorderJobProgress(10)
        failed_job.started(12)
        failed_job.finished(15, "OSError")
        row = coordinator.pv_restore_states["sensor.pv"]
        row["_recorder_job"] = job
        row["last_error"]["recorder"] = failed_job.diagnostics(15)
        discovery = {"devices": {"inverter:1": {"poll_quality": {"status": "partial"}}}}
        hub = SimpleNamespace(
            discovery_diagnostics=Mock(return_value=discovery),
            _plant_host="redacted", _plant_port=502, plant_id=247,
            inverter_count=0, ac_charger_count=0, read_only=True,
            probe_retry_delay=60,
        )
        entry = SimpleNamespace(entry_id="test", as_dict=lambda: {"entry_id": "test"})
        device = SimpleNamespace(
            name="plant", model="test", manufacturer="test", sw_version=None,
            serial_number=None, identifiers={("sigen", "test_plant")}, connections=set(),
        )
        hass = SimpleNamespace(data={"sigen": {"test": {"coordinator": coordinator, "hub": hub}}})
        entry_result = await namespace["async_get_config_entry_diagnostics"](hass, entry)
        device_result = await namespace["async_get_device_diagnostics"](hass, entry, device)
        for result, health_key in ((entry_result, "coordinator"), (device_result, "coordinator_status")):
            self.assertEqual(7, result[health_key]["partial_update_count"])
            self.assertEqual("partial", result["discovery"]["devices"]["inverter:1"]["poll_quality"]["status"])
            self.assertEqual("6333.170261123", result["pv_restore"]["sensor.pv"]["native_value_kwh"])
            self.assertEqual("TimeoutError", result["pv_restore"]["sensor.pv"]["last_error"]["type"])
            self.assertEqual("complete", result["pv_restore"]["sensor.pv"]["recorder_job_phase"])
            self.assertEqual(8, result["pv_restore"]["sensor.pv"]["recorder_queue_wait"])
            self.assertEqual(4, result["pv_restore"]["sensor.pv"]["recorder_query_time"])
            self.assertEqual(rejection, result["pv_restore"]["sensor.pv"]["recorder_latest_state_rejection"])
            self.assertEqual(3, result["pv_restore"]["sensor.pv"]["last_error"]["recorder"]["recorder_query_time"])
            self.assertFalse(any(key.startswith("_") for key in result["pv_restore"]["sensor.pv"]))
            json.dumps(result)
        self.assertEqual(2, hub.discovery_diagnostics.call_count)


if __name__ == "__main__":
    unittest.main()
