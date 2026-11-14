"""Bounded recovery uses fresh reads without replaying protected writes."""
import asyncio
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from modbus_connection import ModbusConnectionError
import test_polling_recovery as recovery
from test_remote_ems_write_readback import MODE, _make_coordinator


class TestWriteInterruptedRead(IsolatedAsyncioTestCase):
    setUp = recovery.TestPollingRecovery.setUp
    make_group = recovery.TestPollingRecovery.make_group
    support_all = recovery.TestPollingRecovery.support_all

    async def test_single_interruption_recovers_in_same_coordinator_update(self):
        self.support_all()
        c, hub, _ = _make_coordinator()
        hub.async_read_plant_data = self.group.async_update
        hub.get_poll_diagnostics = lambda _: self.group.poll_diagnostics()
        original = self.unit.read_input_registers
        calls = []
        async def read(address, count):
            calls.append(address)
            if calls == [100, 500]:
                await self.group.async_write("set_first", 999)
                self.unit.load_raw({"input": {100: 999}})
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read), \
             patch.object(self.unit, "write_register", wraps=self.unit.write_register) as write:
            await c.async_refresh()
        self.assertTrue(c.last_update_success)
        self.assertEqual(0, c.update_failure_count)
        self.assertEqual(999, c.data["plant"]["first"])
        self.assertEqual([100, 500, 100, 500], calls)
        self.assertEqual(1, write.call_count)
        self.assertEqual(1, self.group.poll_write_recoveries)

    async def test_write_bursts_recover_with_fresh_ems_readback(self):
        for interruptions in (2, 3, 6):
            with self.subTest(interruptions=interruptions):
                self.group = self.make_group()
                self.support_all()
                self.unit.load_raw({"input": {100: 123}})
                c, hub, _ = _make_coordinator()
                original = self.unit.read_input_registers
                writes_remaining = interruptions
                calls = []

                async def read_plant():
                    values = await self.group.async_update()
                    return {**values, MODE: values["first"]}

                async def write_mode(*, value, **kwargs):
                    await self.group.async_write("set_first", value)
                    self.unit.load_raw({"input": {100: value}})

                async def read(address, count):
                    nonlocal writes_remaining
                    calls.append(address)
                    if address == 500 and writes_remaining:
                        await self.group.async_write("set_first", writes_remaining)
                        writes_remaining -= 1
                    return await original(address, count)

                hub.async_read_plant_data = read_plant
                hub.async_write_parameter = write_mode
                await c.async_refresh()
                self.assertEqual(123, c.data["plant"][MODE])
                with patch.object(self.unit, "read_input_registers", read), \
                     patch.object(self.unit, "write_register", wraps=self.unit.write_register) as write:
                    await c.async_write_parameter("plant", None, MODE, 999)
                self.assertEqual(interruptions + 1, write.call_count)
                self.assertEqual([100, 500] * (interruptions + 1), calls)
                self.assertTrue(c.last_update_success)
                self.assertEqual(999, c.data["plant"][MODE])
                self.assertEqual(0, c.update_failure_count)
                self.assertEqual(1, self.group.poll_write_restarts)
                self.assertEqual(1, self.group.poll_write_recoveries)
                self.assertEqual(0, self.group.poll_write_recovery_failures)

    async def test_sustained_writes_are_bounded_by_the_coordinator_deadline(self):
        self.support_all()
        coordinator, hub, namespace = _make_coordinator()
        hub.async_read_plant_data = self.group.async_update
        await coordinator.async_refresh()
        original = self.unit.read_input_registers
        original_timeout = namespace["async_timeout"].timeout
        deadline = None
        writes = 0

        def timeout(seconds):
            nonlocal deadline
            deadline = original_timeout(seconds)
            return deadline

        async def read(address, count):
            nonlocal writes
            if address == 500:
                await self.group.async_write("set_first", 999)
                writes += 1
                if writes == 6:
                    deadline.reschedule(asyncio.get_running_loop().time())
            return await original(address, count)

        with patch.object(self.unit, "read_input_registers", read), \
             patch.object(namespace["async_timeout"], "timeout", timeout):
            await coordinator.async_refresh()

        self.assertFalse(coordinator.last_update_success)
        self.assertEqual(1, coordinator.update_failure_count)
        self.assertEqual(1, coordinator.update_timeout_count)
        self.assertEqual(6, writes)
        self.assertEqual(1, self.group.poll_write_restarts)
        self.assertEqual(1, self.group.poll_write_recovery_failures)
        self.assertIsNone(self.group._read_round)
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)

    async def test_abandoned_round_does_not_queue_remaining_blocks(self):
        self.support_all()
        original = self.unit.read_input_registers
        calls = []

        async def read(address, count):
            calls.append(address)
            if calls == [100]:
                await self.group.async_write("set_first", 999)
                self.unit.load_raw({"input": {100: 999}})
            return await original(address, count)

        with patch.object(self.unit, "read_input_registers", read):
            self.assertEqual(999, (await self.group.async_update())["first"])
        self.assertEqual([100, 100, 500], calls)

    async def test_rejected_saved_round_write_gets_one_fresh_retry(self):
        for validator in (AsyncMock(return_value=False), None):
            with self.subTest(identity_validator=validator is not None):
                self.group = self.make_group()
                self.support_all()
                self.group._confirm_endpoint_identity = validator
                self.unit.load_raw({"input": {100: 123}})
                original = self.unit.read_input_registers
                started, finish = asyncio.Event(), asyncio.Event()
                writes, calls = [], []
                async def read(address, count):
                    calls.append(address)
                    if address == 500:
                        if not finish.is_set():
                            started.set()
                            await finish.wait()
                        elif not writes:
                            writes.append(999)
                            await self.group.async_write("set_first", 999)
                            self.unit.load_raw({"input": {100: 999}})
                    return await original(address, count)
                with patch.object(self.unit, "read_input_registers", read), \
                     patch.object(self.unit, "write_register", wraps=self.unit.write_register) as write:
                    interrupted = asyncio.create_task(self.group.async_update())
                    try:
                        await started.wait()
                        interrupted.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await interrupted
                        self.assertEqual([123], self.group._read_round["words"]["telemetry"]["first"])
                    finally:
                        finish.set()
                        await asyncio.gather(interrupted, return_exceptions=True)
                    result = await self.group.async_update()
                self.assertEqual(999, result["first"])
                self.assertEqual([100, 500, 100, 500, 100, 500], calls)
                self.assertEqual(1, write.call_count)
                self.assertEqual(1, self.group.poll_write_restarts)
                self.assertEqual(1, self.group.poll_write_recoveries)
                self.assertEqual(0, self.group.poll_write_recovery_failures)

    async def test_identity_reset_during_saved_round_validation_does_not_retry(self):
        self.support_all()
        self.group._read_round = {"started": self.devices.time.monotonic(), "words": {},
                                  "attempted": {}, "identity": {"fixture": "stable"}}
        async def validate(expected):
            self.group.reset_poll_progress()
            # A subsequent write must not hide the identity reset.
            await self.group.async_write("set_first", 999)
            return True
        self.group._confirm_endpoint_identity = validate
        with patch.object(self.unit, "read_input_registers", wraps=self.unit.read_input_registers) as read:
            with self.assertRaisesRegex(self.devices.ModbusError, "identity validation"):
                await self.group.async_update()
        self.assertEqual(0, read.call_count)
        self.assertEqual(0, self.group.poll_write_restarts)

    async def test_identity_reset_never_retries_even_if_followed_by_write(self):
        self.support_all()
        original = self.unit.read_input_registers
        async def read(address, count):
            self.group.reset_poll_progress()
            await self.group.async_write("set_first", 999)
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read):
            with self.assertRaises(self.devices.ModbusError):
                await self.group.async_update()
        self.assertEqual(0, self.group.poll_write_restarts)

    async def test_connection_error_does_not_trigger_write_retry(self):
        self.support_all()
        with patch.object(self.unit, "read_input_registers", side_effect=ModbusConnectionError("offline")) as read:
            with self.assertRaises(ModbusConnectionError):
                await self.group.async_update()
        self.assertEqual(1, read.call_count)
        self.assertEqual(0, self.group.poll_write_restarts)

    async def test_cancelled_writer_survives_reader_timeout_and_next_poll_drains_it(self):
        self.support_all()
        sibling = self.make_group()
        sibling._endpoint_writes = self.group._endpoint_writes
        sibling._on_write = lambda: self.group.reset_poll_progress(reason="write")
        started, release = asyncio.Event(), asyncio.Event()
        original_read, original_write = self.unit.read_input_registers, self.unit.write_register
        writer = None
        calls = []
        async def slow_write(*args):
            started.set()
            await release.wait()
            await original_write(*args)
            self.unit.load_raw({"input": {100: 999}})
        async def read(address, count):
            nonlocal writer
            calls.append(address)
            if calls == [100, 500]:
                writer = asyncio.create_task(sibling.async_write("set_first", 999))
                await started.wait()
                writer.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await writer
            return await original_read(address, count)
        with patch.object(self.unit, "read_input_registers", read), \
             patch.object(self.unit, "write_register", slow_write):
            poll = asyncio.create_task(self.group.async_update())
            try:
                await started.wait()
                # The existing coordinator deadline cancels this wait; no
                # replacement reads may be queued behind the surviving write.
                while self.group.poll_write_restarts == 0:
                    await asyncio.sleep(0)
                poll.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await poll
                self.assertEqual([100, 500], calls)
                self.assertTrue(self.group._endpoint_writes.pending)
            finally:
                release.set()
                await self.group._endpoint_writes.async_drain()
                await asyncio.gather(poll, return_exceptions=True)
            self.assertEqual(999, (await self.group.async_update())["first"])

    async def test_multiple_endpoint_writes_are_drained_before_retry(self):
        self.support_all()
        original = self.unit.read_input_registers
        calls, writers = [], []
        async def read(address, count):
            calls.append(address)
            if calls == [100, 500]:
                writers.extend(asyncio.create_task(self.group.async_write("set_first", value))
                               for value in (777, 999))
                await asyncio.gather(*writers)
                self.unit.load_raw({"input": {100: 999}})
            return await original(address, count)
        with patch.object(self.unit, "read_input_registers", read):
            self.assertEqual(999, (await self.group.async_update())["first"])
        self.assertEqual(1, self.group.poll_write_restarts)
        self.assertEqual(1, self.group.poll_write_recoveries)
        self.assertEqual(4, len(calls))

    async def test_identity_change_while_waiting_for_writer_blocks_recovery(self):
        self.support_all()
        original = self.unit.read_input_registers
        async def read(address, count):
            self.group._on_write()
            return await original(address, count)
        async def drain():
            self.group.reset_poll_progress()
        with patch.object(self.unit, "read_input_registers", read), \
             patch.object(self.group._endpoint_writes, "async_drain", drain):
            with self.assertRaises(self.devices.ModbusError):
                await self.group.async_update()
        self.assertEqual(1, self.group.poll_write_recovery_failures)

    async def test_real_coordinator_deadline_bounds_wait_for_sibling_write(self):
        self.support_all()
        c, hub, _ = _make_coordinator()
        hub.async_read_plant_data = self.group.async_update
        original = self.unit.read_input_registers
        release = asyncio.Event()
        async def read(address, count):
            self.group._on_write()
            return await original(address, count)
        async def drain():
            # Expire the coordinator's existing timeout while recovering.
            deadline.reschedule(asyncio.get_running_loop().time())
            await release.wait()
        namespace = c._async_update_data.__func__.__globals__
        original_timeout = namespace["async_timeout"].timeout
        def timeout(seconds):
            nonlocal deadline
            deadline = original_timeout(seconds)
            return deadline
        deadline = None
        with patch.object(self.unit, "read_input_registers", read), \
             patch.object(self.group._endpoint_writes, "async_drain", drain), \
             patch.object(namespace["async_timeout"], "timeout", timeout):
            await c.async_refresh()
        self.assertFalse(c.last_update_success)
        self.assertEqual(1, c.update_timeout_count)
        self.assertEqual(1, self.group.poll_write_restarts)

    async def test_waits_for_late_sibling_write_then_reads_fresh_values(self):
        self.support_all()
        sibling = self.make_group()
        sibling._endpoint_writes = self.group._endpoint_writes
        sibling._on_write = lambda: self.group.reset_poll_progress(reason="write")
        original_read, original_write = self.unit.read_input_registers, self.unit.write_register
        started, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        drain = self.group._endpoint_writes.async_drain
        calls, writers = [], []
        async def slow_write(*args):
            started.set()
            await release.wait()
            await original_write(*args)
            self.unit.load_raw({"input": {100: 999}})
        async def read(address, count):
            calls.append(address)
            if calls == [100, 500]:
                writers.append(asyncio.create_task(sibling.async_write("set_first", 999)))
                await started.wait()
            return await original_read(address, count)
        async def observed_drain():
            waiting.set()
            await drain()
        with patch.object(self.unit, "read_input_registers", read), \
             patch.object(self.unit, "write_register", slow_write), \
             patch.object(self.group._endpoint_writes, "async_drain", observed_drain):
            poll = asyncio.create_task(self.group.async_update())
            try:
                await waiting.wait()
                self.assertFalse(poll.done())
                self.assertEqual([100, 500], calls)
            finally:
                release.set()
                await asyncio.gather(*writers)
            self.assertEqual(999, (await poll)["first"])
        self.assertEqual([100, 500, 100, 500], calls)


class TestEndpointWriteBinding(IsolatedAsyncioTestCase):
    async def test_hub_shares_only_writes_on_the_same_endpoint(self):
        import test_incremental_discovery as fixtures
        from test_modbus_hub import _ConfigEntry
        fixture = fixtures.TestHubDiscoveryCache()
        fixture.setUp()
        data = deepcopy(fixture.data)
        data["inverter_connections"]["Other"] = {"host": "other", "port": 502, "slave_id": 1}
        hub = fixture.modbus.SigenergyModbusHub(object(), _ConfigEntry(data))
        groups = list(hub._all_groups())
        plant = hub._plant._group
        for _, info, group in groups:
            self.assertEqual(info["host"] != "other", group._endpoint_writes is plant._endpoint_writes)
        await hub.async_close()
