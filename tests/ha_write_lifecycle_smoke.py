"""Exercise actual HA service cancellation and shared confirmation refresh locks."""
import asyncio
import logging
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from custom_components.sigen.coordinator import SigenergyDataUpdateCoordinator
from custom_components.sigen.write_lifecycle import CURRENT_COMMAND, WriteAdmission


async def main():
    with TemporaryDirectory(prefix="sigen-ha-write-") as directory:
        hass = HomeAssistant(directory)
        frame.async_setup(hass)
        admission = WriteAdmission()
        group = SimpleNamespace(_reset_epoch=0, _write_allowed=lambda: True,
                                is_supported=lambda _: True, _pending_requests=set(),
                                _on_write=lambda: None, _endpoint_poll=None)
        component = SimpleNamespace(write=AsyncMock())
        entered = asyncio.Event()
        callers = []

        async def write(**kwargs):
            callers.append(CURRENT_COMMAND.get().caller)
            entered.set()
            await admission.write(group, component, kwargs["register_name"], kwargs["value"])

        hub = SimpleNamespace(_stopping=False, async_write_parameter=write,
            config_entry=SimpleNamespace(async_create_background_task=lambda _h, coro, **kw:
                hass.async_create_background_task(coro, **kw)))
        # Standalone Windows runs have no loaded integration frame for HA's
        # deprecation reporter. Keep its actual coordinator/service machinery.
        with patch.object(frame, "report_usage"):
            coordinator = SigenergyDataUpdateCoordinator(hass, logging.getLogger(__name__),
                                                        hub, "write smoke", 5)

        async def handler(call):
            await coordinator.async_write_parameter("plant", None, "target", 1)

        hass.services.async_register("sigen_test", "write", handler)
        try:
            await admission.lock.acquire()
            service = asyncio.create_task(hass.services.async_call("sigen_test", "write", blocking=True))
            await asyncio.wait_for(entered.wait(), 5)
            service.cancel()
            await asyncio.gather(service, return_exceptions=True)
            admission.lock.release()
            if admission.local:
                await asyncio.gather(*admission.local, return_exceptions=True)
            assert callers[0].cancelling() > 0
            component.write.assert_not_awaited()

            values = {"plant_remote_ems_enable": 0, "plant_remote_ems_control_mode": 0}
            async def apply(register, value):
                values[register] = value
            component.write.side_effect = apply
            first_started, finish_first = asyncio.Event(), asyncio.Event()
            polls = []
            async def fetch():
                coordinator._update_sequence += 1
                sequence = coordinator._update_sequence
                snapshot = dict(values)
                polls.append(sequence)
                if len(polls) == 1:
                    first_started.set()
                    await finish_first.wait()
                coordinator._last_successful_update_sequence = sequence
                return {"plant": snapshot}
            coordinator._async_update_data = fetch
            old = asyncio.create_task(coordinator.async_refresh())
            await first_started.wait()
            a = asyncio.create_task(coordinator.async_write_parameter(
                "plant", None, "plant_remote_ems_control_mode", 2))
            while not getattr(coordinator, "_verifiers", {}):
                await asyncio.sleep(0)
            b = asyncio.create_task(coordinator.async_write_parameter(
                "plant", None, "plant_remote_ems_enable", 1))
            while len(coordinator._verifiers) < 2:
                await asyncio.sleep(0)
            a.cancel()
            await asyncio.gather(a, return_exceptions=True)
            assert not old.done()
            assert not coordinator._confirmation_task.cancelled()
            finish_first.set()
            await asyncio.gather(old, b)
            assert polls == [1, 2], polls
            assert coordinator._ems_commands == 0
            print("HA service cancellation and shared fresh-poll confirmation pass")
        finally:
            await hass.async_stop(force=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 30))
