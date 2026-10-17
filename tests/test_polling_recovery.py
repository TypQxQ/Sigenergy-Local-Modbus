"""Real Modbus mocks: interrupted startup, persisted jobs and resumed reads."""
from __future__ import annotations

import __future__
import ast
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from modbus_connection import ModbusConnectionError, ModbusTimeoutError
from modbus_connection.exceptions import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection
from modbus_connection.model import Component, gauge

import test_incremental_discovery as fixtures
from test_modbus_hub import _ConfigEntry, _load_sigen_module
from test_remote_ems_write_readback import _make_coordinator

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "sigen"


class TestPollingRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.devices = _load_sigen_module("sigen_devices")
        self.unit = MockModbusConnection().for_unit(1)
        self.unit.load_raw({"input": {100: 123, 101: 456, 500: 789}})
        self.group = self.make_group()

    def make_group(self, support=None):
        component = type("RecoveryFixture", (Component,), {
            "register_space": "input", "first": gauge(100, 1.0, writable=True),
            "second": gauge(101, 1.0), "separate": gauge(500, 1.0),
        })(self.unit)
        commands = type("RecoveryCommands", (Component,), {
            "register_space": "holding", "set_first": gauge(100, 1.0, writable=True),
        })(self.unit)
        group = self.devices._ComponentGroup({"telemetry": component}, {"commands": commands})
        group.enable_incremental(support or {}, lambda: None, "test")
        # The standalone transport fixture has a stable endpoint. Hub tests
        # separately exercise the real shared-endpoint identity validator.
        group._identity_snapshot = lambda: {"fixture": "stable"}
        group._confirm_endpoint_identity = AsyncMock(return_value=True)
        return group

    def support_all(self):
        self.group._field_support["telemetry"].update(first=True, second=True, separate=True)

    async def test_expired_bootstrap_words_are_read_again_after_an_outage(self):
        self.support_all()
        clock = [100.0]
        with patch.object(self.devices, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            await self.group.async_bootstrap({"first", "second"})
            self.unit.load_raw({"input": {100: 999, 101: 888}})
            clock[0] = 281.0
            with patch.object(self.unit, "read_input_registers",
                              wraps=self.unit.read_input_registers) as read:
                data = await self.group.async_update()
            self.assertEqual((999, 888), (data["first"], data["second"]))
            self.assertEqual([(100, 2), (500, 1)], [call.args for call in read.call_args_list])

    async def test_fresh_bootstrap_words_keep_their_age_and_avoid_duplicate_reads(self):
        self.support_all()
        clock = [100.0]
        with patch.object(self.devices, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            await self.group.async_bootstrap({"first", "second"})
            clock[0] = 150.0
            with patch.object(self.unit, "read_input_registers",
                              wraps=self.unit.read_input_registers) as read:
                self.assertEqual(123, (await self.group.async_update())["first"])
            self.assertEqual([(500, 1)], [call.args for call in read.call_args_list])
            self.assertEqual(50.0, self.group.poll_last_round_span)

    async def test_bootstrap_words_expiring_during_the_first_poll_are_not_published(self):
        self.support_all()
        clock = [100.0]
        with patch.object(self.devices, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            await self.group.async_bootstrap({"first", "second"})
            clock[0] = 279.0
            original = self.unit.read_input_registers
            async def read(address, count):
                clock[0] = 281.0
                return await original(address, count)
            with patch.object(self.unit, "read_input_registers", read):
                with self.assertRaises(self.devices.ModbusError):
                    await self.group.async_update()
            self.assertEqual(181.0, self.group.poll_last_round_span)
            self.assertIsNone(self.group._read_round)

    async def test_cancelled_first_poll_does_not_leave_bootstrap_timestamps_in_the_next_round(self):
        self.support_all()
        clock = [100.0]
        started, finish = asyncio.Event(), asyncio.Event()
        original = self.unit.read_input_registers
        async def read(address, count):
            if address == 500 and not finish.is_set():
                started.set()
                await finish.wait()
            return await original(address, count)
        with patch.object(self.devices, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            await self.group.async_bootstrap({"first", "second"})
            with patch.object(self.unit, "read_input_registers", read):
                task = asyncio.create_task(self.group.async_update())
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                clock[0] = 281.0
                self.unit.load_raw({"input": {100: 999}})
                finish.set()
                self.assertEqual(999, (await self.group.async_update())["first"])
                self.assertEqual({}, self.group._prefetched_at)

    async def test_deadline_resume_keeps_late_reply_and_does_not_repeat_completed_reads(self):
        self.support_all()
        started, finish = asyncio.Event(), asyncio.Event()
        original = self.unit.read_input_registers
        calls = []
        async def read(address, count):
            calls.append((address, count))
            if address == 500:
                started.set()
                await finish.wait()
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read):
            first = asyncio.create_task(self.group.async_update())
            await started.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            resumed = asyncio.create_task(self.group.async_update())
            await asyncio.sleep(0)
            self.assertEqual([(100, 2), (500, 1)], calls)
            self.assertFalse(resumed.done())
            finish.set()
            self.assertEqual({"first": 123, "second": 456, "separate": 789}, await resumed)
            self.assertEqual([(100, 2), (500, 1)], calls)
            self.assertEqual(1, self.group.poll_resumes)
            # The next ordinary round reads every field again, including metadata.
            await self.group.async_update()
            self.assertEqual([(100, 2), (500, 1)] * 2, calls)

    async def test_link_failure_resumes_only_unfinished_blocks_and_retains_support(self):
        self.support_all()
        original = self.unit.read_input_registers
        calls = []
        failed = False
        async def read(address, count):
            nonlocal failed
            calls.append((address, count))
            if address == 500 and not failed:
                failed = True
                raise ModbusConnectionError("connection lost")
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read):
            with self.assertRaises(ModbusConnectionError):
                await self.group.async_update()
            self.assertTrue(all(self.group._field_support["telemetry"].values()))
            data = await self.group.async_update()
        self.assertEqual(789, data["separate"])
        self.assertEqual([(100, 2), (500, 1), (500, 1)], calls)

    async def test_saved_round_without_an_endpoint_identity_validator_is_discarded(self):
        self.support_all()
        original = self.unit.read_input_registers
        async def interrupted(address, count):
            if address == 500:
                raise ModbusConnectionError("offline")
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", interrupted):
            with self.assertRaises(ModbusConnectionError):
                await self.group.async_update()
        self.group._confirm_endpoint_identity = None
        self.unit.load_raw({"input": {100: 999}})
        self.assertEqual(999, (await self.group.async_update())["first"])
        self.assertEqual(0, self.group.poll_resumes)

    async def test_coordinator_runtime_deadline_recovers_without_stacking_or_repeating_reads(self):
        self.support_all()
        coordinator, hub, namespace = _make_coordinator()
        hub.async_read_plant_data = self.group.async_update
        coordinator._last_successful_update_sequence = 1
        deadlines = []
        def deadline(seconds):
            timer = asyncio.timeout(seconds)
            deadlines.append(timer)
            return timer
        namespace["async_timeout"].timeout = deadline
        original = self.unit.read_input_registers
        finish = asyncio.Event()
        calls = []
        async def read(address, count):
            calls.append((address, count))
            if address == 500:
                # Expire only when the protected request is in flight. Keep
                # the retry's real deadline independent of host load.
                deadlines[0].reschedule(asyncio.get_running_loop().time())
                await finish.wait()
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read):
            await coordinator.async_refresh()
            self.assertFalse(coordinator.last_update_success)
            self.assertEqual(1, coordinator.update_timeout_count)
            self.assertTrue(self.group._pending_requests)
            retry = asyncio.create_task(coordinator.async_refresh())
            await asyncio.sleep(0)
            self.assertEqual([(100, 2), (500, 1)], calls)
            finish.set()
            await retry
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual(123, coordinator.data["plant"]["first"])
        self.assertEqual(789, coordinator.data["plant"]["separate"])
        self.assertEqual([(100, 2), (500, 1)], calls)

    async def test_expired_progress_is_discarded_instead_of_publishing_old_values(self):
        self.support_all()
        original = self.unit.read_input_registers
        async def fail_last(address, count):
            if address == 500:
                raise ModbusConnectionError("offline")
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", fail_last):
            with self.assertRaises(ModbusConnectionError):
                await self.group.async_update()
        self.group._read_round["started"] = time.monotonic() - 181
        self.unit.load_raw({"input": {100: 999}})
        self.assertEqual(999, (await self.group.async_update())["first"])
        self.assertEqual(1, self.group.poll_expired_rounds)

    async def test_write_invalidates_saved_samples_before_readback(self):
        self.support_all()
        original = self.unit.read_input_registers
        async def fail_last(address, count):
            if address == 500:
                raise ModbusConnectionError("offline")
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", fail_last):
            with self.assertRaises(ModbusConnectionError):
                await self.group.async_update()
        self.assertEqual([123], self.group._read_round["words"]["telemetry"]["first"])
        await self.group.async_write("set_first", 999)
        # This fixture reads input space; mimic the device's control readback.
        self.unit.load_raw({"input": {100: 999}})
        self.assertIsNone(self.group._read_round)
        self.assertEqual(999, (await self.group.async_update())["first"])

    async def test_round_expiring_during_resume_is_not_published(self):
        self.support_all()
        self.group._read_round = {"started": 0, "words": {"telemetry": {
            "first": [123], "second": [456]}}, "attempted": {"telemetry": {"first", "second"}}}
        clock = [170.0]
        original = self.unit.read_input_registers
        async def read(address, count):
            clock[0] = 181.0
            return await original(address, count)
        with patch.object(self.devices, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
             patch.object(self.unit, "read_input_registers", read):
            with self.assertRaises(self.devices.ModbusError):
                await self.group.async_update()
        self.assertIsNone(self.group._read_round)
        self.assertEqual(1, self.group.poll_expired_rounds)

    async def test_write_during_old_poll_prevents_old_frame_from_being_published(self):
        self.support_all()
        original = self.unit.read_input_registers
        started, finish = asyncio.Event(), asyncio.Event()
        async def slow_last(address, count):
            if address == 500:
                started.set()
                await finish.wait()
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", slow_last):
            old = asyncio.create_task(self.group.async_update())
            await started.wait()
            await self.group.async_write("set_first", 999)
            self.unit.load_raw({"input": {100: 999}})
            finish.set()
            with self.assertRaises(self.devices.ModbusError):
                await old
            self.assertEqual(999, (await self.group.async_update())["first"])

    async def test_persisted_split_jobs_resume_without_repeating_the_rejected_parent(self):
        self.unit.fail_read(101, IllegalDataAddressError(2), register_type="input")
        self.group.queue_discovery()
        await self.group.async_discover_one()  # Reject the 100-101 block.
        progress = json.loads(json.dumps(self.group.discovery_progress("boot")))
        resumed = self.make_group(deepcopy(self.group._field_support))
        resumed.restore_discovery(progress, "boot")
        original = self.unit.read_input_registers
        calls = []
        async def read(address, count):
            calls.append((address, count))
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read):
            while resumed._probe_queue:
                await resumed.async_discover_one()
        self.assertNotIn((100, 2), calls)
        self.assertIs(resumed.is_supported("first"), True)
        self.assertIs(resumed.is_supported("second"), False)
        self.assertNotIn("words", progress)

    async def test_checked_negative_retries_once_per_boot_not_once_per_setup_attempt(self):
        self.group._field_support["telemetry"].update(first=False, second=True, separate=True)
        self.unit.fail_read(100, IllegalDataAddressError(2), register_type="input")
        self.group.queue_discovery()
        await self.group.async_discover_one()
        progress = json.loads(json.dumps(self.group.discovery_progress("boot")))
        same_boot = self.make_group(deepcopy(self.group._field_support))
        same_boot.restore_discovery(progress, "boot")
        self.assertFalse(same_boot._probe_queue)
        next_boot = self.make_group(deepcopy(self.group._field_support))
        next_boot.restore_discovery(progress, "new-boot")
        self.assertEqual([("telemetry", ("first",))], list(next_boot._probe_queue))

    async def test_discovery_failure_backoff_survives_recreated_group(self):
        self.group.queue_discovery()
        async def silent(address, count):
            raise ModbusTimeoutError("silent")
        with patch.object(self.unit, "read_input_registers", silent):
            with self.assertRaises(ModbusTimeoutError):
                await self.group.async_discover_one()
            self.assertGreater(self.group.discovery_retry_at - time.monotonic(), 59)
            progress = json.loads(json.dumps(self.group.discovery_progress("boot")))
            resumed = self.make_group()
            resumed.restore_discovery(progress, "boot")
            self.assertGreater(resumed.discovery_retry_at - time.monotonic(), 58)
            with self.assertRaises(ModbusTimeoutError):
                await resumed.async_discover_one()
            self.assertGreater(resumed.discovery_retry_at - time.monotonic(), 119)
        self.assertFalse(resumed._field_support["telemetry"])

    async def test_malformed_saved_jobs_cannot_expand_reads_across_address_gaps(self):
        self.group.restore_discovery({"pending": [None, ["bad", ["first"]],
            ["telemetry", ["first", 123, "typo", "separate"]]], "checked": [None]}, "boot")
        jobs = list(self.group._probe_queue)
        self.assertIn(("telemetry", ("first",)), jobs)
        self.assertIn(("telemetry", ("separate",)), jobs)
        self.assertNotIn(("telemetry", ("first", "separate")), jobs)


class TestStartupRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.TestHubDiscoveryCache("test_changed_firmware_does_not_restore_cached_negatives")
        self.fixture.setUp()
        self.modbus = self.fixture.modbus
        self.hubs = []
        self.setup_tasks, self.gates = [], []
        self.entry = _ConfigEntry({**self.fixture.data, "name": "Test Plant"})
        self.entry.title = "Test Plant"
        self.entry.add_update_listener = lambda callback: lambda: None
        self.entry.async_create_background_task = lambda hass, coro, **kwargs: asyncio.create_task(coro)
        self.hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock()))
        self.namespace = self.load_setup()

    def load_setup(self):
        class NotReady(Exception):
            pass
        def deadline(seconds):
            self.setup_deadline = asyncio.timeout(seconds)
            return self.setup_deadline
        def hub_factory(hass, entry):
            hub = self.modbus.SigenergyModbusHub(hass, entry)
            self.hubs.append(hub)
            return hub
        def coordinator_factory(hass, logger, hub, **kwargs):
            coordinator, _, namespace = _make_coordinator()
            coordinator.hub, coordinator.hass, coordinator.data = hub, hass, None
            coordinator.update_interval = timedelta(seconds=5)
            namespace["CONF_INVERTER_HAS_DCCHARGER"] = "inverter_has_dccharger"
            async def first_refresh():
                await coordinator.async_refresh()
                if not coordinator.last_update_success:
                    raise NotReady("First refresh failed")
            coordinator.async_config_entry_first_refresh = first_refresh
            coordinator.async_add_listener = lambda callback: lambda: None
            return coordinator
        tree = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
        setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "async_setup_entry")
        namespace = {"asyncio": SimpleNamespace(timeout=deadline, CancelledError=asyncio.CancelledError),
            "_LOGGER": Mock(), "STARTUP_TIMEOUT": 5,
            "ConfigEntryNotReady": NotReady, "SigenergyModbusHub": hub_factory,
            "SigenergyDataUpdateCoordinator": coordinator_factory,
            "CONF_PLANT_CONNECTION": "plant_connection", "CONF_SCAN_INTERVAL": "scan_interval",
            "DEFAULT_SCAN_INTERVAL": 5, "CONF_HOST": "host", "CONF_PORT": "port",
            "CONF_NAME": "name", "DOMAIN": "sigen", "PLATFORMS": ["sensor"],
            "register_parent_devices": Mock(), "generate_device_id": Mock(),
            "async_update_options": Mock()}
        exec(compile(ast.Module(body=[setup], type_ignores=[]), "__init__.py", "exec",
                     flags=__future__.annotations.compiler_flag), namespace)
        return namespace

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        for task in self.setup_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.setup_tasks, return_exceptions=True)
        for hub in self.hubs:
            await hub.async_close()

    async def test_actual_setup_timeout_checkpoints_first_identity_reads_then_new_hub_recovers(self):
        unit = self.fixture.inv_unit
        original = unit.read_input_registers
        field = self.fixture.hub._inverters["Inverter"].running_info.declared_fields[
            "inverter_pv_string_count"]
        started, finish = asyncio.Event(), asyncio.Event()
        self.gates.append(finish)
        calls = []
        async def slow_identity(address, count):
            calls.append((address, count))
            if address == field.address and not finish.is_set():
                self.setup_deadline.reschedule(asyncio.get_running_loop().time() + 0.02)
                started.set()
                await finish.wait()
            return await original(address, count)
        with patch.object(self.modbus, "SupportCache", side_effect=lambda *args: self.fixture.cache()), \
             patch.object(unit, "read_input_registers", slow_identity):
            first = asyncio.create_task(self.namespace["async_setup_entry"](self.hass, self.entry))
            self.setup_tasks.append(first)
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.sleep(0.04)  # Real, scaled setup deadline, not a mocked exception.
            self.assertFalse(first.done(), "cleanup must drain the protected wire request")
            pending_snapshot = self.fixture.store.pending()
            support_maps = [record["support"] for record in pending_snapshot["devices"].values()]
            self.assertTrue(any(states.get("running_info", {}).get("inverter_serial_number")
                                for states in support_maps), "first identity response must be checkpointed")
            finish.set()
            with self.assertRaises(self.namespace["ConfigEntryNotReady"]):
                await first
            self.assertGreater(self.fixture.store.saves, 0)
            self.assertFalse(self.hubs[0]._inverters["Inverter"]._inverter_group._pending_requests)
            self.namespace["STARTUP_TIMEOUT"] = 2
            self.assertTrue(await self.namespace["async_setup_entry"](self.hass, self.entry))
        self.assertEqual(2, len(self.hubs))
        self.assertIs(self.hubs[1]._inverters["Inverter"].is_supported("inverter_serial_number"), True)
        self.assertGreaterEqual(sum(address == 30500 for address, _ in calls), 2,
                                "identity must still be validated with fresh reads")
        self.assertIs(self.hass.data["sigen"][self.entry.entry_id]["hub"], self.hubs[1])

    async def test_truncated_pv_count_defers_platform_setup_until_a_new_hub_reads_the_count(self):
        unit = self.fixture.inv_unit
        field = self.fixture.hub._inverters["Inverter"].running_info.declared_fields["inverter_pv_string_count"]
        unit.load_raw({"input": {field.address: 2}})
        original = unit.read_input_registers
        async def truncated(address, count):
            if address <= field.address < address + count:
                return []
            return await original(address, count)
        with patch.object(self.modbus, "SupportCache", side_effect=lambda *args: self.fixture.cache()):
            with patch.object(unit, "read_input_registers", truncated):
                with self.assertRaises(self.namespace["ConfigEntryNotReady"]):
                    await self.namespace["async_setup_entry"](self.hass, self.entry)
            self.hass.config_entries.async_forward_entry_setups.assert_not_awaited()
            self.assertIsNot(self.hubs[0]._inverters["Inverter"].is_supported("inverter_pv_string_count"), False)
            self.assertTrue(await self.namespace["async_setup_entry"](self.hass, self.entry))
        self.assertEqual(2, len(self.hubs))
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        coordinator = self.hass.data["sigen"][self.entry.entry_id]["coordinator"]
        self.assertEqual(2, coordinator.data["inverters"]["Inverter"]["inverter_pv_string_count"])

    async def test_first_coordinator_poll_timeout_keeps_cache_and_next_setup_uses_new_hub(self):
        await self.fixture.hub.async_initialize(self.fixture.cache())
        for key, endpoint, _role, group in self.fixture.hub._bindings:
            for name, component in group._readable.items():
                group._field_support[name].update({field: True for field in component.declared_fields})
            self.fixture.hub._checkpoint(key, endpoint, group)
        await self.fixture.hub._cache.async_flush()
        original = self.fixture.plant_unit.read_input_registers
        started, finish = asyncio.Event(), asyncio.Event()
        self.gates.append(finish)
        async def slow_poll(address, count):
            if not finish.is_set():
                self.setup_deadline.reschedule(asyncio.get_running_loop().time() + 0.02)
                started.set()
                await finish.wait()
            return await original(address, count)
        with patch.object(self.modbus, "SupportCache", side_effect=lambda *args: self.fixture.cache()), \
             patch.object(self.fixture.plant_unit, "read_input_registers", slow_poll):
            first = asyncio.create_task(self.namespace["async_setup_entry"](self.hass, self.entry))
            self.setup_tasks.append(first)
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.sleep(0.04)
            finish.set()
            with self.assertRaises(self.namespace["ConfigEntryNotReady"]):
                await first
            self.assertTrue(all(group.cache_status == "hit" for _, _, _, group in self.hubs[0]._bindings))
            self.namespace["STARTUP_TIMEOUT"] = 2
            self.assertTrue(await self.namespace["async_setup_entry"](self.hass, self.entry))
        self.assertTrue(all(group.cache_status == "hit" for _, _, _, group in self.hubs[1]._bindings))
        for record in self.fixture.store.data["devices"].values():
            self.assertNotIn("words", record)
            self.assertNotIn("values", record)
            self.assertNotIn("words", record.get("discovery", {}))

    async def test_new_hub_restores_split_discovery_jobs_from_the_store(self):
        hub = self.fixture.hub
        self.hubs.append(hub)
        await hub.async_initialize(self.fixture.cache())
        group = hub._plant._group
        group._probe_queue.clear()
        group._queued_fields.clear()
        names = ("plant_phase_a_active_power", "plant_phase_b_active_power")
        group._enqueue("running_info", names)
        fields = group._readable["running_info"].declared_fields
        self.fixture.plant_unit.fail_read(fields[names[1]].address,
            IllegalDataAddressError(2), register_type="input")
        await group.async_discover_one()
        split = list(group._probe_queue)
        self.assertEqual([("running_info", (name,)) for name in names], split)
        await hub.async_close()
        restored = self.modbus.SigenergyModbusHub(self.hass, self.entry)
        self.hubs.append(restored)
        await restored.async_initialize(self.fixture.cache())
        self.assertEqual("hit", restored._plant._group.cache_status)
        self.assertEqual(split, list(restored._plant._group._probe_queue)[:2])
        restored._coordinator = SimpleNamespace(last_update_success=True,
                                                update_interval=timedelta(seconds=5))
        with patch.object(self.entry, "async_create_background_task", Mock()) as schedule:
            restored.async_schedule_discovery()
            schedule.assert_not_called()  # Reload cannot bypass the interval cap.

    async def test_write_resets_read_progress_only_on_the_affected_endpoint(self):
        data = deepcopy(self.fixture.data)
        data["plant_connection"]["read_only"] = False
        data["inverter_connections"]["Other"] = {"host": "other", "port": 502,
                                                 "slave_id": 1}
        hub = self.modbus.SigenergyModbusHub(self.hass, _ConfigEntry(data))
        self.hubs.append(hub)
        groups = list(hub._all_groups())
        for _role, _info, group in groups:
            group._read_round = {"started": time.monotonic(), "words": {}, "attempted": {}}
        await hub.async_write_parameter("plant", None, "plant_remote_ems_enable", 1)
        for _role, info, group in groups:
            if info["host"] == "other":
                self.assertIsNotNone(group._read_round)
            else:
                self.assertIsNone(group._read_round)
