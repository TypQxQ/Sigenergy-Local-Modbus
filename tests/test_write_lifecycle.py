"""Cancellation and handoff exercise the actual endpoint admission worker."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from test_sigen_components import _load_sigen_module


class TestWriteLifecycle(IsolatedAsyncioTestCase):
    def setUp(self):
        self.m = _load_sigen_module("write_lifecycle")
        self.admission = self.m.WriteAdmission()
        self.group = SimpleNamespace(
            _reset_epoch=0, _write_allowed=lambda: True,
            is_supported=lambda key: True, _pending_requests=set(),
            _on_write=lambda: None, _endpoint_poll=None)
        self.component = SimpleNamespace(write=AsyncMock())

    async def test_cancelled_lock_waiter_never_invokes_component(self):
        await self.admission.lock.acquire()
        caller = asyncio.create_task(self.admission.write(self.group,self.component,"target",1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.admission.lock.release()
        caller.cancel()
        await asyncio.gather(caller,return_exceptions=True)
        await asyncio.sleep(0)
        self.component.write.assert_not_awaited()
        self.assertEqual(0,self.admission.waiting)
        self.assertFalse(self.admission.pending)

    async def test_expired_command_is_not_sent(self):
        ctx=self.m.Command(asyncio.current_task(),asyncio.get_running_loop().time()-1)
        token=self.m.CURRENT_COMMAND.set(ctx)
        try:
            with self.assertRaisesRegex(self.m.CommandRejected,"expired"):
                await self.admission.write(self.group,self.component,"target",1)
        finally:
            self.m.CURRENT_COMMAND.reset(token)
        self.component.write.assert_not_awaited()
        self.assertFalse(self.admission.lock.locked())

    async def test_handed_off_write_keeps_lock_after_caller_cancellation(self):
        started,finish=asyncio.Event(),asyncio.Event()
        async def write(*args):
            started.set()
            await finish.wait()
        self.component.write.side_effect=write
        caller=asyncio.create_task(self.admission.write(self.group,self.component,"target",1))
        await started.wait()
        caller.cancel()
        await asyncio.gather(caller,return_exceptions=True)
        self.assertTrue(self.admission.lock.locked())
        self.assertEqual(1,len(self.admission.pending))
        finish.set()
        await self.admission.async_drain()
        self.assertFalse(self.admission.lock.locked())
        self.component.write.assert_awaited_once()

    async def test_task_cancelled_before_first_step_does_not_leak_capacity(self):
        # Admission creates its child synchronously before its first wait.
        caller=asyncio.create_task(self.admission.write(self.group,self.component,"target",1))
        await asyncio.sleep(0)
        for task in list(self.admission.local):
            task.cancel()
        await asyncio.gather(caller,return_exceptions=True)
        await asyncio.sleep(0)
        self.assertEqual(0,self.admission.waiting)

    async def test_queue_capacity_rejects_seventeenth_local_command(self):
        await self.admission.lock.acquire()
        callers = [asyncio.create_task(self.admission.write(
            self.group, self.component, "target", value)) for value in range(16)]
        try:
            while self.admission.waiting < 16:
                await asyncio.sleep(0)
            with self.assertRaises(self.m.CommandRejected) as rejected:
                await self.admission.write(self.group, self.component, "target", 17)
            self.assertEqual("command_queue_full", rejected.exception.translation_key)
            self.component.write.assert_not_awaited()
        finally:
            for caller in callers:
                caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            self.admission.lock.release()
            if self.admission.local:
                await asyncio.gather(*self.admission.local, return_exceptions=True)
        self.assertEqual(0, self.admission.waiting)

    async def test_cancelled_hub_close_does_not_cancel_handed_off_write(self):
        from test_modbus_hub import _load_sigen_module as load_hub
        modbus = load_hub("modbus")
        hub = modbus.SigenergyModbusHub.__new__(modbus.SigenergyModbusHub)
        hub._discovery_tasks = {}
        hub._endpoint_polls = {("test", 502): SimpleNamespace(writes=self.admission)}
        hub._all_groups = lambda: [("plant", {}, self.group)]
        hub._cache = None
        self.group.reset_poll_progress = lambda: None
        started, finish = asyncio.Event(), asyncio.Event()
        async def write(*args):
            started.set()
            await finish.wait()
        self.component.write.side_effect = write
        caller = asyncio.create_task(self.admission.write(self.group, self.component, "target", 1))
        await started.wait()
        closer = asyncio.create_task(hub.async_close())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        closer.cancel()
        await asyncio.gather(closer, return_exceptions=True)
        self.assertTrue(self.admission.lock.locked())
        self.assertTrue(self.admission.pending)
        finish.set()
        await caller
        await hub.async_close()
        self.assertFalse(self.admission.pending)
        self.component.write.assert_awaited_once()

    async def test_reset_and_readonly_guard_reject_before_handoff(self):
        self.group._write_allowed=lambda:False
        with self.assertRaisesRegex(self.m.CommandRejected,"invalidated"):
            await self.admission.write(self.group,self.component,"target",1)
        self.component.write.assert_not_awaited()

    async def test_handoff_callback_failure_does_not_leak_admission(self):
        def fail():
            raise RuntimeError("callback failed")
        ctx = self.m.Command(asyncio.current_task(), asyncio.get_running_loop().time()+10,
                             on_handoff=fail)
        token = self.m.CURRENT_COMMAND.set(ctx)
        try:
            with self.assertRaisesRegex(RuntimeError, "callback failed"):
                await self.admission.write(self.group, self.component, "target", 1)
        finally:
            self.m.CURRENT_COMMAND.reset(token)
        self.assertEqual(0, self.admission.waiting)
        self.assertFalse(self.admission.pending)
        self.assertFalse(self.admission.lock.locked())
        self.component.write.assert_not_awaited()

    async def test_handled_timeout_does_not_poison_later_command(self):
        try:
            async with asyncio.timeout(0):
                await asyncio.sleep(0)
        except TimeoutError:
            pass
        await self.admission.write(self.group,self.component,"target",1)
        self.component.write.assert_awaited_once()

    async def test_identity_observation_retires_a_waiter_before_reset_drain(self):
        await self.admission.lock.acquire()
        caller=asyncio.create_task(self.admission.write(self.group,self.component,"target",1))
        while not self.admission.local:
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.group._command_epoch=1
        self.admission.lock.release()
        with self.assertRaisesRegex(self.m.CommandRejected,"invalidated"):
            await caller
        self.component.write.assert_not_awaited()

    async def test_failed_connect_does_not_consume_recycle_cooldown(self):
        from modbus_connection import ModbusTimeoutError
        from test_modbus_hub import _load_sigen_module as load_hub
        modbus=load_hub("modbus")
        poll=modbus._EndpointPoll(("test",502),self.admission)
        self.group._endpoint_poll=poll
        self.component.modbus_unit=SimpleNamespace(connected=False,disconnect=AsyncMock())
        self.component.write.side_effect=ModbusTimeoutError("connect timeout")
        with self.assertRaises(ModbusTimeoutError):
            await self.admission.write(self.group,self.component,"target",1)
        self.assertEqual(float("-inf"),poll.last_reconnect)
        self.assertEqual(0,poll.reconnects)
        self.component.modbus_unit.disconnect.assert_not_awaited()

    def test_writable_field_shape_has_no_implicit_pre_read(self):
        from modbus_connection.model import Component
        from modbus_connection.model.fields import PackedBitsField
        from modbus_connection.mock import MockModbusConnection
        components=_load_sigen_module("sigen_components")
        unit=MockModbusConnection().for_unit(1)
        writable=0
        for cls in vars(components).values():
            if not isinstance(cls,type) or not issubclass(cls,Component) or cls is Component:
                continue
            for field in cls(unit).declared_fields.values():
                if getattr(field,"writable",False):
                    writable+=1
                    self.assertNotIsInstance(field,PackedBitsField)
                    self.assertIsNone(field.scale_register)
        self.assertGreater(writable,0)
