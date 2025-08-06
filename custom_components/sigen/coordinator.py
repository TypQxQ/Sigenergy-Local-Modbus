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
from .const import CONF_INVERTER_HAS_DCCHARGER, DEFAULT_SCAN_INTERVAL_HIGH, DEFAULT_SCAN_INTERVAL_MEDIUM, DEFAULT_SCAN_INTERVAL_LOW, DEFAULT_SCAN_INTERVAL_ALARM
from .modbusregisterdefinitions import UpdateFrequencyType

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
        high_scan_interval: int,
        alarm_scan_interval: int,
        medium_scan_interval: int,
        low_scan_interval: int,
    ) -> None:
        """Initialize."""
        self.hub = hub
        self.platforms = []
        self.largest_update_interval : float = 0.0
        self.data: dict[str, Any] | None = None
        # Ensure high_scan_interval is not zero to prevent division by zero
        if high_scan_interval <= 0:
            high_scan_interval = DEFAULT_SCAN_INTERVAL_HIGH
            logger.warning("Invalid high_scan_interval <= 0, defaulting to %s", DEFAULT_SCAN_INTERVAL_HIGH)

        self._high_update_ratio = 1 # High always runs
        self._alarm_update_ratio = max(1, alarm_scan_interval // high_scan_interval)
        self._medium_update_ratio = max(1, medium_scan_interval // high_scan_interval)
        self._low_update_ratio = max(1, low_scan_interval // high_scan_interval)
        # Initialize counter so the first run triggers LOW frequency
        self._update_counter = self._low_update_ratio - 1
        # Initialize latest fetch time tracking
        self._latest_fetch_times: Dict[UpdateFrequencyType, float] = {
            freq: 0.0 for freq in UpdateFrequencyType
        }

        super().__init__(
            hass,
            logger,
            name=name,
            update_interval= update_interval,
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via Modbus library."""
        try:
            async with async_timeout.timeout(60):
                update_interval = self.update_interval.total_seconds() if self.update_interval else DEFAULT_SCAN_INTERVAL_HIGH
                start_time = dt_util.utcnow()

                # Increment counter and determine update frequency for this run
                self._update_counter += 1
                current_frequency_type: UpdateFrequencyType
                if self._update_counter % self._low_update_ratio == 0:
                    current_frequency_type = UpdateFrequencyType.LOW
                    self._update_counter = 0 # Reset counter after LOW run
                elif self._update_counter % self._medium_update_ratio == 0:
                    current_frequency_type = UpdateFrequencyType.MEDIUM
                elif self._update_counter % self._alarm_update_ratio == 0:
                    current_frequency_type = UpdateFrequencyType.ALARM
                else:
                    current_frequency_type = UpdateFrequencyType.HIGH

                # _LOGGER.debug("Update cycle %s: Requesting frequency %s", self._update_counter, current_frequency_type.name)

                # Fetch plant data with the determined frequency
                plant_data = await self.hub.async_read_plant_data(update_frequency=current_frequency_type)

                # Fetch inverter data for each inverter
                inverter_data = {}
                dc_charger_data = {}
                for inverter_name in self.hub.inverter_connections.keys():
                    # Fetch inverter data with the determined frequency
                    inverter_data[inverter_name] = await self.hub.async_read_inverter_data(inverter_name, update_frequency=current_frequency_type)
                    # Fetch DC charger data if the inverter supports it
                    if self.hub.inverter_connections[inverter_name].get(CONF_INVERTER_HAS_DCCHARGER, False):
                        dc_charger_data[inverter_name] = await self.hub.async_read_dc_charger_data(inverter_name, update_frequency=current_frequency_type)

                # Fetch AC charger data for each AC charger
                ac_charger_data = {}
                for ac_charger_name in self.hub.ac_charger_connections.keys():
                    # Fetch AC charger data with the determined frequency
                    ac_charger_data[ac_charger_name] = await self.hub.async_read_ac_charger_data(ac_charger_name, update_frequency=current_frequency_type)

                # --- Merge fetched data into existing coordinator data ---
                if self.data is None:
                    # First successful run, initialize self.data
                    # Assumes the first run (LOW frequency) fetches a complete enough initial state
                    self.data = {
                        "plant": plant_data,
                        "inverters": inverter_data,
                        "ac_chargers": ac_charger_data,
                        "dc_chargers": dc_charger_data,
                    }
                else:
                    # Merge new data into the existing self.data structure
                    self.data["plant"].update(plant_data)

                    # Ensure nested dictionaries exist before updating
                    if "inverters" not in self.data:
                        self.data["inverters"] = {}
                    if "dc_chargers" not in self.data:
                        self.data["dc_chargers"] = {}
                    for inverter_name, inv_data in inverter_data.items():
                        if inverter_name not in self.data["inverters"]:
                            self.data["inverters"][inverter_name] = {}
                        self.data["inverters"][inverter_name].update(inv_data)

                    if "ac_chargers" not in self.data:
                        self.data["ac_chargers"] = {}
                    for charger_name, chg_data in ac_charger_data.items():
                        if charger_name not in self.data["ac_chargers"]:
                            self.data["ac_chargers"][charger_name] = {}
                        self.data["ac_chargers"][charger_name].update(chg_data)
                # --- End Merge ---

                timetaken = (dt_util.utcnow() - start_time).total_seconds()

                # Update latest fetch time and max fetch time
                if self.largest_update_interval < timetaken:
                    self.largest_update_interval = timetaken
                self._latest_fetch_times[current_frequency_type] = timetaken

                log_level = logging.WARNING if timetaken > update_interval \
                    and self._update_counter > 1 else logging.DEBUG
                self.logger.log(
                    log_level,
                    "Fetching data for %s took %.3f seconds%s",
                    current_frequency_type.name,
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
            # Schedule the next update to be LOW frequency to quickly reflect the change
            self._update_counter = self._low_update_ratio - 1
            _LOGGER.debug("Scheduled next update as LOW frequency after writing %s to %s '%s'",
                          value, device_type, device_identifier or 'plant')
            # Trigger an immediate refresh if desired, though the scheduled one might be sufficient
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
