"""Shared serialization preserves read ownership and refreshes invalidated rounds."""
import asyncio
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
import test_polling_recovery as recovery

class TestSerializedWrites(IsolatedAsyncioTestCase):
    setUp = recovery.TestPollingRecovery.setUp
    make_group = recovery.TestPollingRecovery.make_group
    support_all = recovery.TestPollingRecovery.support_all

    async def test_write_between_blocks_restarts_with_current_measurements(self):
        self.support_all()
        started,finish=asyncio.Event(),asyncio.Event()
        original=self.unit.read_input_registers
        first=True
        async def read(address,count):
            nonlocal first
            if first:
                first=False
                started.set()
                await finish.wait()
            return await original(address,count)
        original_write=self.unit.write_register
        async def write(*args,**kwargs):
            await original_write(*args,**kwargs)
            self.unit.load_raw({"input":{100:999}})
        with patch.object(self.unit,"read_input_registers",read),patch.object(self.unit,"write_register",write):
            poll=asyncio.create_task(self.group.async_update())
            await started.wait()
            writer=asyncio.create_task(self.group.async_write("set_first",999))
            while not self.group._endpoint_writes.waiting:
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            finish.set()
            result=await poll
            await writer
            self.assertEqual(999,result["first"])
            self.assertEqual(999,(await self.group.async_update())["first"])

    async def test_cancelled_reader_keeps_lock_until_wire_read_finishes(self):
        self.support_all()
        started,finish=asyncio.Event(),asyncio.Event()
        original=self.unit.read_input_registers
        async def read(address,count):
            started.set()
            await finish.wait()
            return await original(address,count)
        with patch.object(self.unit,"read_input_registers",read):
            poll=asyncio.create_task(self.group.async_update())
            await started.wait()
            poll.cancel()
            await asyncio.gather(poll,return_exceptions=True)
            self.assertTrue(self.group._endpoint_writes.lock.locked())
            finish.set()
            while self.group._pending_requests:
                await asyncio.sleep(0)
            self.assertFalse(self.group._endpoint_writes.lock.locked())

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

    async def test_hub_write_resets_every_group_on_the_same_endpoint(self):
        import test_incremental_discovery as fixtures
        from test_modbus_hub import _ConfigEntry
        fixture = fixtures.TestHubDiscoveryCache()
        fixture.setUp()
        data = deepcopy(fixture.data)
        data["inverter_connections"]["Other"] = {"host": "other", "port": 502, "slave_id": 1}
        hub = fixture.modbus.SigenergyModbusHub(object(), _ConfigEntry(data))
        groups = list(hub._all_groups())
        for _, _, group in groups:
            group._read_round = {"started": 0.0, "words": {}, "attempted": {}, "failures": {}}
        hub._inverters["Inverter"]._inverter_group._on_write()
        for _, info, group in groups:
            self.assertEqual(info["host"] == "other", group._read_round is not None)
        await hub.async_close()
