"""Data update coordinator for Sigenergy ESS."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional, Union # Added Optional, Union

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed  # pylint: disable=syntax-error
from homeassistant.util import dt as dt_util

from .modbus import SigenergyModbusHub, SigenergyModbusError # Added SigenergyModbusError
from .const import CONF_INVERTER_HAS_DCCHARGER, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class SigenergyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Sigenergy ESS."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        hub: SigenergyModbusHub,
        name: str,
        scan_interval: int,
    ) -> None:
        """Initialize."""
        self.hub = hub
        self.platforms = []
        self.largest_update_interval : float = 0.0
        self.latest_fetch_time: float = 0.0
        self.data: dict[str, Any] | None = None

        if scan_interval <= 1:
            scan_interval = DEFAULT_SCAN_INTERVAL
            logger.warning("Invalid scan_interval <= 1, defaulting to %s", DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via Modbus library."""
        device_tasks: set[asyncio.Task] = set()
        try:
            async with async_timeout.timeout(60):
                start_time = dt_util.utcnow()

                # Fetch all data at once
                plant_data = await self.hub.async_read_plant_data()

                # Fetch inverter and DC charger data concurrently
                inverter_tasks = {
                    name: asyncio.create_task(self.hub.async_read_inverter_data(name))
                    for name in self.hub.inverter_connections
                }
                dc_tasks = {
                    name: asyncio.create_task(self.hub.async_read_dc_charger_data(name))
                    for name, cfg in self.hub.inverter_connections.items()
                    if cfg.get(CONF_INVERTER_HAS_DCCHARGER, False)
                }
                device_tasks.update(inverter_tasks.values())
                device_tasks.update(dc_tasks.values())
                inverter_data: dict[str, Any] = {}
                for name, task in inverter_tasks.items():
                    try:
                        inverter_data[name] = await task
                    except Exception as e:
                        _LOGGER.error("Error fetching inverter %s data: %s", name, e)
                        inverter_data[name] = {}
                dc_charger_data: dict[str, Any] = {}
                for name, task in dc_tasks.items():
                    try:
                        dc_charger_data[name] = await task
                    except Exception as e:
                        _LOGGER.error("Error fetching DC charger %s data: %s", name, e)
                        dc_charger_data[name] = {}

                # Fetch AC charger data concurrently
                ac_tasks = {
                    name: asyncio.create_task(self.hub.async_read_ac_charger_data(name))
                    for name in self.hub.ac_charger_connections
                }
                device_tasks.update(ac_tasks.values())
                ac_charger_data: dict[str, Any] = {}
                for name, task in ac_tasks.items():
                    try:
                        ac_charger_data[name] = await task
                    except Exception as e:
                        _LOGGER.error("Error fetching AC charger %s data: %s", name, e)
                        ac_charger_data[name] = {}

                # Merge fetched data into existing coordinator data
                # Preserve the _sensors_initialized flag across updates
                sensors_initialized = self.data.get("_sensors_initialized", False) if self.data else False
                self.data = {
                    "plant": plant_data,
                    "inverters": inverter_data,
                    "ac_chargers": ac_charger_data,
                    "dc_chargers": dc_charger_data,
                    "_sensors_initialized": sensors_initialized,  # Preserve flag across updates
                }
                _LOGGER.debug("RBS-coordinator-106-coordinator-post Merged fetched, len: %s data: %s", len(self.data), self.data)
                
                timetaken = (dt_util.utcnow() - start_time).total_seconds()
                self.latest_fetch_time = timetaken

                # Update latest fetch time and max fetch time
                if self.largest_update_interval < timetaken:
                    self.largest_update_interval = timetaken

                # log_level = logging.WARNING if timetaken > update_interval else logging.DEBUG
                # self.logger.log(
                #     log_level,
                #     "Fetching all data took %.3f seconds%s",
                #     timetaken,
                #     " - exceeds update interval!" if timetaken > update_interval else ""
                # )

                # Return the updated, complete data structure
                return self.data
        except asyncio.TimeoutError as exception:
            raise UpdateFailed("Timeout communicating with Sigenergy system") from exception
        except Exception as exception:
            raise UpdateFailed(f"Error communicating with Sigenergy system: {exception}") from exception
        finally:
            # A timeout/cancellation otherwise stops only the child currently
            # awaited, leaving sibling polls running into the next update.
            # Cancel the read workflows and retrieve every result. The device
            # layer still lets each in-flight wire transaction finish safely.
            for task in device_tasks:
                if not task.done():
                    task.cancel()
            if device_tasks:
                await asyncio.gather(*device_tasks, return_exceptions=True)

    async def async_write_parameter(
        self,
        device_type: str,
        device_identifier: Optional[str],
        register_name: str,
        value: Union[int, float, str]
    ) -> None:
        """Write a parameter via the Modbus hub and schedule a update."""

        async def _write_and_refresh() -> None:
            try:
                await self.hub.async_write_parameter(
                    device_type=device_type,
                    device_identifier=device_identifier,
                    register_name=register_name,
                    value=value,
                )
                # Trigger an immediate refresh
                await self.async_request_refresh()
            except SigenergyModbusError as ex:
                _LOGGER.error("Failed to write parameter %s to %s '%s': %s",
                              register_name, device_type, device_identifier or 'plant', ex)
                raise
            except Exception as ex:
                _LOGGER.error("Unexpected error writing parameter %s to %s '%s': %s",
                              register_name, device_type, device_identifier or 'plant', ex)
                raise

        def _consume_result(task: asyncio.Task[None]) -> None:
            # Errors are logged with their device/register context inside
            # the task, even if the caller has already been cancelled.
            if not task.cancelled():
                task.exception()

        # Keep the task tracked by the config entry after caller cancellation.
        task = self.hub.config_entry.async_create_task(
            self.hass, _write_and_refresh(), name=f"Sigenergy write {register_name}"
        )
        task.add_done_callback(_consume_result)

        # Cancelling the service caller must not cancel the write/refresh.
        # wait() leaves the tracked task running and avoids shield()'s
        # separate late-exception reports on Python 3.14.
        await asyncio.wait({task})
        task.result()

    def mark_sensors_initialized(self) -> None:
        """Mark that static sensors have been initialized and calculated sensors can run."""
        if self.data is not None:
            self.data["_sensors_initialized"] = True
            _LOGGER.debug("Static sensors marked as initialized - calculated sensors can now execute")
