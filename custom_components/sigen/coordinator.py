"""Data update coordinator for Sigenergy ESS."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional, Union # Added Optional, Union
from typing import Any, Dict

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed  # pylint: disable=syntax-error
from homeassistant.util import dt as dt_util

from .modbus import SigenergyModbusHub, SigenergyModbusError # Added SigenergyModbusError
from .modbus import SigenergyModbusHub
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

        if scan_interval <= 0:
            scan_interval = DEFAULT_SCAN_INTERVAL
            logger.warning("Invalid scan_interval <= 0, defaulting to %s", DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via Modbus library."""
        try:
            async with async_timeout.timeout(60):
                update_interval = self.update_interval.total_seconds() if self.update_interval else DEFAULT_SCAN_INTERVAL
                start_time = dt_util.utcnow()

                # Fetch all data at once
                plant_data = await self.hub.async_read_plant_data()

                # Fetch inverter data for each inverter
                inverter_data = {}
                dc_charger_data = {}
                for inverter_name in self.hub.inverter_connections.keys():
                    # Fetch inverter data
                    inverter_data[inverter_name] = await self.hub.async_read_inverter_data(inverter_name)
                    # Fetch DC charger data if the inverter supports it
                    if self.hub.inverter_connections[inverter_name].get(CONF_INVERTER_HAS_DCCHARGER, False):
                        dc_charger_data[inverter_name] = await self.hub.async_read_dc_charger_data(inverter_name)

                # Fetch AC charger data for each AC charger
                ac_charger_data = {}
                for ac_charger_name in self.hub.ac_charger_connections.keys():
                    # Fetch AC charger data
                    ac_charger_data[ac_charger_name] = await self.hub.async_read_ac_charger_data(ac_charger_name)

                # Merge fetched data into existing coordinator data
                self.data = {
                    "plant": plant_data,
                    "inverters": inverter_data,
                    "ac_chargers": ac_charger_data,
                    "dc_chargers": dc_charger_data,
                }

                timetaken = (dt_util.utcnow() - start_time).total_seconds()
                self.latest_fetch_time = timetaken

                # Update latest fetch time and max fetch time
                if self.largest_update_interval < timetaken:
                    self.largest_update_interval = timetaken

                log_level = logging.WARNING if timetaken > update_interval else logging.DEBUG
                self.logger.log(
                    log_level,
                    "Fetching all data took %.3f seconds%s",
                    timetaken,
                    " - exceeds update interval!" if timetaken > update_interval else ""
                )

                # Return the updated, complete data structure
                return self.data
        except asyncio.TimeoutError as exception:
            raise UpdateFailed("Timeout communicating with Sigenergy system") from exception
        except Exception as exception:
            raise UpdateFailed(f"Error communicating with Sigenergy system: {exception}") from exception

    async def async_write_parameter(
        self,
        device_type: str,
        device_identifier: Optional[str],
        register_name: str,
        value: Union[int, float, str]
    ) -> None:
        """Write a parameter via the Modbus hub and schedule a low-frequency update."""
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
            # Re-raise the exception so the calling entity knows the write failed
            raise
        except Exception as ex:
            _LOGGER.error("Unexpected error writing parameter %s to %s '%s': %s",
                          register_name, device_type, device_identifier or 'plant', ex)
            # Re-raise for visibility
            raise
