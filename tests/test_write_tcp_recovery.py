"""Actual TCP frames: a lost reply never causes command replay."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from modbus_connection import ModbusTcpParams, ModbusTimeoutError
from modbus_connection.tmodbus import ModbusConnection
from modbus_connection.pymodbus import ModbusConnection as PymodbusConnection
from modbus_connection.model import Component, gauge
from test_modbus_hub import _load_sigen_module


class Commands(Component):
    register_space = "holding"
    target = gauge(400, 1.0, writable=True)


class TestWriteTCP(IsolatedAsyncioTestCase):
    async def test_lost_ack_recycles_without_resending(self):
        await self.exercise_backend(ModbusConnection)

    async def test_pymodbus_lost_ack_is_not_replayed(self):
        await self.exercise_backend(PymodbusConnection)

    async def exercise_backend(self, connection_class):
        frames=[]
        handlers=set()
        async def serve(reader,writer):
            task=asyncio.current_task()
            handlers.add(task)
            try:
                while True:
                    header=await reader.readexactly(7)
                    size=int.from_bytes(header[4:6],"big")
                    body=await reader.readexactly(size-1)
                    frames.append(body)
                    if len(frames)>1:
                        writer.write(header+body)
                        await writer.drain()
            except (asyncio.IncompleteReadError,ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)
        server=await asyncio.start_server(serve,"127.0.0.1",0)
        port=server.sockets[0].getsockname()[1]
        conn=connection_class(ModbusTcpParams(host="127.0.0.1",port=port),timeout=0.05)
        unit=conn.for_unit(247)
        lifecycle=_load_sigen_module("write_lifecycle")
        modbus=_load_sigen_module("modbus")
        admission=lifecycle.WriteAdmission()
        endpoint=modbus._EndpointPoll(("127.0.0.1",port),admission)
        group=SimpleNamespace(_reset_epoch=0,_write_allowed=lambda:True,
            is_supported=lambda _:True,_pending_requests=set(),_on_write=lambda:None,
            _endpoint_poll=endpoint)
        try:
            with self.assertRaises(ModbusTimeoutError):
                await admission.write(group,Commands(unit),"target",7)
            self.assertEqual(1,len(frames))
            if connection_class is ModbusConnection:
                self.assertEqual(1,endpoint.reconnects)
            else:
                self.assertLessEqual(endpoint.reconnects,1)
            self.assertFalse(unit.connected)
            await admission.write(group,Commands(unit),"target",9)
            self.assertEqual(2,len(frames))
            self.assertEqual(7,int.from_bytes(frames[0][-2:],"big"))
            self.assertEqual(9,int.from_bytes(frames[1][-2:],"big"))
        finally:
            await conn.close()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.gather(*handlers)
