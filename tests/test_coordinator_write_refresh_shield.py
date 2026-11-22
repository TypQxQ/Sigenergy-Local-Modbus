"""Service caller identity must survive delegation without a protected wrapper."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_remote_ems_write_readback import _make_coordinator

class TestCoordinatorCaller(IsolatedAsyncioTestCase):
    async def test_actual_caller_is_forwarded_and_local_cancellation_prevents_write(self):
        c,hub,ns=_make_coordinator()
        m=__import__("sigen.write_lifecycle",fromlist=["x"])
        admission=m.WriteAdmission()
        group=SimpleNamespace(_reset_epoch=0,_write_allowed=lambda:True,
            is_supported=lambda key:True,_pending_requests=set(),_on_write=lambda:None,
            _endpoint_poll=None)
        component=SimpleNamespace(write=AsyncMock())
        observed=[]
        async def write(**kwargs):
            observed.append(m.CURRENT_COMMAND.get().caller)
            await admission.write(group,component,"target",1)
        hub.async_write_parameter=write
        await admission.lock.acquire()
        caller=asyncio.create_task(c.async_write_parameter("plant",None,"target",1))
        while not admission.local:
            await asyncio.sleep(0)
        admission.lock.release()
        caller.cancel()
        await asyncio.gather(caller,return_exceptions=True)
        await asyncio.sleep(0)
        self.assertIs(observed[0],caller)
        component.write.assert_not_awaited()

    async def test_ordinary_refresh_is_background_and_not_cancelled_by_service(self):
        c,hub,_=_make_coordinator()
        m=__import__("sigen.write_lifecycle",fromlist=["x"])
        started,finish=asyncio.Event(),asyncio.Event()
        contexts=[]
        async def refresh():
            contexts.append(m.CURRENT_COMMAND.get())
            started.set()
            await finish.wait()
        c.async_request_refresh=refresh
        await c.async_write_parameter("plant",None,"target",1)
        await started.wait()
        self.assertTrue(hub.tasks)
        finish.set()
        await asyncio.gather(*hub.tasks)
        self.assertEqual([None], contexts)

    async def test_timeout_after_handoff_refreshes_after_caller_is_uncancelled(self):
        c,hub,_=_make_coordinator()
        m=__import__("sigen.write_lifecycle",fromlist=["x"])
        admission=m.WriteAdmission()
        group=SimpleNamespace(_command_epoch=0,_write_allowed=lambda:True,
            is_supported=lambda key:True,_pending_requests=set(),_on_write=lambda:None,
            _endpoint_poll=None)
        finish=asyncio.Event()
        scopes=[]
        contexts=[]
        async def component_write(*args):
            scopes[0].reschedule(asyncio.get_running_loop().time())
            await finish.wait()
        component=SimpleNamespace(write=component_write)
        async def write(**kwargs):
            await admission.write(group,component,"target",1)
        async def refresh():
            contexts.append(m.CURRENT_COMMAND.get())
        hub.async_write_parameter=write
        c.async_request_refresh=refresh
        async def caller():
            try:
                async with asyncio.timeout(None) as scope:
                    scopes.append(scope)
                    await c.async_write_parameter("plant",None,"target",1)
            except TimeoutError:
                self.assertEqual(0,asyncio.current_task().cancelling())
        await caller()
        self.assertTrue(admission.pending)
        finish.set()
        await admission.async_drain()
        if hub.tasks:
            await asyncio.gather(*hub.tasks)
        self.assertEqual([None],contexts)

    async def test_confirmation_poll_has_no_command_context(self):
        c,hub,_=_make_coordinator()
        m=__import__("sigen.write_lifecycle",fromlist=["x"])
        original=hub.async_read_plant_data
        contexts=[]
        async def read():
            contexts.append(m.CURRENT_COMMAND.get())
            return await original()
        hub.async_read_plant_data=read
        await c.async_write_parameter("plant",None,"plant_remote_ems_enable",1)
        self.assertEqual([None],contexts)
