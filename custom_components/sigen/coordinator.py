"""Data update coordinator for Sigenergy ESS."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN
from .modbus import SigenergyModbusHub

_LOGGER = logging.getLogger(__name__)


class SigenergyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Sigenergy ESS."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        hub: SigenergyModbusHub,
        name: str,
        update_interval: timedelta,
    ) -> None:
        """Initialize."""
        self.hub = hub
        self.platforms = []

        super().__init__(
            hass,
            logger,
            name=name,
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via Modbus library."""
        try:
            async with async_timeout.timeout(60):
                # Fetch plant data
                plant_data = await self.hub.async_read_plant_data()

                # Fetch inverter data for each inverter
                inverter_data = {}
                for inverter_id in self.hub.inverter_slave_ids:
                    inverter_data[inverter_id] = await self.hub.async_read_inverter_data(inverter_id)

                # Fetch AC charger data for each AC charger
                ac_charger_data = {}
                for ac_charger_id in self.hub.ac_charger_slave_ids:
                    ac_charger_data[ac_charger_id] = await self.hub.async_read_ac_charger_data(ac_charger_id)

                # Fetch DC charger data for each DC charger
                dc_charger_data = {}
                for dc_charger_id in self.hub.dc_charger_slave_ids:
                    dc_charger_data[dc_charger_id] = await self.hub.async_read_dc_charger_data(dc_charger_id)
                
                # Combine all data
                _LOGGER.debug("[CS][Coordinator] Plant data keys: %s", list(plant_data.keys()) if plant_data else None)
                data = {
                    "plant": plant_data,
                    "inverters": inverter_data,
                    "ac_chargers": ac_charger_data,
                    "dc_chargers": dc_charger_data,
                }
                
                return data
        except asyncio.TimeoutError as exception:
            raise UpdateFailed("Timeout communicating with Sigenergy system") from exception
        except Exception as exception:
            raise UpdateFailed(f"Error communicating with Sigenergy system: {exception}") from exception