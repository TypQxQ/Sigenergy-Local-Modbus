"""Select platform for Sigenergy ESS integration."""
from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Coroutine

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import CONF_NAME#, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
)
from .modbusregisterdefinitions import (RemoteEMSControlMode)
from .coordinator import SigenergyDataUpdateCoordinator # Import coordinator
# from .modbus import SigenergyModbusError
from .common import generate_sigen_entity # Added generate_device_id
from .sigen_entity import SigenergyEntity # Import the new base class

_LOGGER = logging.getLogger(__name__)

# This register is deprecated in Modbus v. 2.7 and is now marked as reserved.
# Map of grid codes to country names
# GRID_CODE_MAP = {
#     1: "Germany",
#     2: "UK",
#     3: "Italy",
#     4: "Spain",
#     5: "Portugal",
#     6: "France",
#     7: "Poland",
#     8: "Hungary",
#     9: "Belgium",
#     10: "Norway",
#     11: "Sweden",
#     12: "Finland",
#     13: "Denmark",
#     19: "Australia",
#     26: "Austria",
#     36: "Ireland",
#     # Add more mappings as they are discovered
# }

# Reverse mapping for looking up codes by country name
# COUNTRY_TO_CODE_MAP = {country: code for code, country in GRID_CODE_MAP.items()}
# Debug log the grid code map

# This register is deprecated in Modbus v. 2.7 and is now marked as reserved.
# def _get_grid_code_display(data, device_name): # Changed inverter_id to device_name
#     """Get the display value for grid code with debug logging."""

#     # Get the raw grid code value using device_name
#     grid_code = data["inverters"].get(device_name, {}).get("inverter_grid_code")

#     # Handle None case
#     if grid_code is None:
#         return "Unknown"

#     # Try to convert to int and look up in map
#     try:
#         grid_code_int = int(grid_code)
#         # _LOGGER.debug("Converted grid code to int: %s", grid_code_int)

#         # Look up in map
#         result = GRID_CODE_MAP.get(grid_code_int)
#         # _LOGGER.debug("Grid code map lookup result: %s", result)

#         if result is not None:
#             return result
#         else:
#             return f"Unknown ({grid_code})"
#     except (ValueError, TypeError) as e:
#         _LOGGER.debug("Error converting grid code for %s: %s", device_name, e)
#         return f"Unknown ({grid_code})"



@dataclass(frozen=True)
class SigenergySelectEntityDescription(SelectEntityDescription):
    """Class describing Sigenergy select entities."""

    # The second argument 'identifier' will be device_name for inverters, device_id otherwise. Default returns empty string.
    current_option_fn: Callable[[Dict[str, Any], Optional[Any]], str] = lambda data, identifier: ""
    # Make select_option_fn async and update type hint
    # Make select_option_fn async and update type hint to accept coordinator
    select_option_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any], str], Coroutine[Any, Any, None]] = lambda coordinator, identifier, option: asyncio.sleep(0) # Placeholder async lambda
    available_fn: Callable[[Dict[str, Any], Optional[Any]], bool] = lambda data, _: True
    entity_registry_enabled_default: bool = True


PLANT_SELECTS = [
    SigenergySelectEntityDescription(
        key="plant_remote_ems_control_mode",
        name="Remote EMS Control Mode",
        icon="mdi:remote",
        options=[
            "PCS Remote Control",
            "Standby",
            "Maximum Self Consumption",
            "Command Charging (Grid First)",
            "Command Charging (PV First)",
            "Command Discharging (PV First)",
            "Command Discharging (ESS First)",
            "Unknown",
        ],
        current_option_fn=lambda data, _: {
            RemoteEMSControlMode.PCS_REMOTE_CONTROL: "PCS Remote Control",
            RemoteEMSControlMode.STANDBY: "Standby",
            RemoteEMSControlMode.MAXIMUM_SELF_CONSUMPTION: "Maximum Self Consumption",
            RemoteEMSControlMode.COMMAND_CHARGING_GRID_FIRST: "Command Charging (Grid First)",
            RemoteEMSControlMode.COMMAND_CHARGING_PV_FIRST: "Command Charging (PV First)",
            RemoteEMSControlMode.COMMAND_DISCHARGING_PV_FIRST: "Command Discharging (PV First)",
            RemoteEMSControlMode.COMMAND_DISCHARGING_ESS_FIRST: "Command Discharging (ESS First)",
        }.get(data["plant"].get("plant_remote_ems_control_mode"), "Unknown"),
        select_option_fn=lambda coordinator, _, option: coordinator.async_write_parameter(
            "plant", None, "plant_remote_ems_control_mode",
            {
                "PCS Remote Control": RemoteEMSControlMode.PCS_REMOTE_CONTROL,
                "Standby": RemoteEMSControlMode.STANDBY,
                "Maximum Self Consumption": RemoteEMSControlMode.MAXIMUM_SELF_CONSUMPTION,
                "Command Charging (Grid First)": RemoteEMSControlMode.COMMAND_CHARGING_GRID_FIRST,
                "Command Charging (PV First)": RemoteEMSControlMode.COMMAND_CHARGING_PV_FIRST,
                "Command Discharging (PV First)": RemoteEMSControlMode.COMMAND_DISCHARGING_PV_FIRST,
                "Command Discharging (ESS First)": RemoteEMSControlMode.COMMAND_DISCHARGING_ESS_FIRST,
            }.get(option, RemoteEMSControlMode.PCS_REMOTE_CONTROL),
        ),
        available_fn=lambda data, _: data["plant"].get("plant_remote_ems_enable") == 1,
        entity_registry_enabled_default=False,
    ),
]

INVERTER_SELECTS = [
    # This register is deprecated in Modbus v. 2.7 and is now marked as reserved.
    # SigenergySelectEntityDescription(
    #     key="inverter_grid_code",
    #     name="Grid Code",
    #     icon="mdi:transmission-tower",
    #     options=list(GRID_CODE_MAP.values()),
    #     entity_category=EntityCategory.CONFIG,
    #     # Use identifier (device_name for inverters)
    #     current_option_fn=lambda data, identifier: _get_grid_code_display(data, identifier),
    #     # Use identifier (device_name for inverters)
    #     select_option_fn=lambda coordinator, identifier, option: coordinator.async_write_parameter(
    #         "inverter", identifier, "inverter_grid_code",
    #         COUNTRY_TO_CODE_MAP.get(option, 0)  # Default to 0 if country not found
    #     ),
    #     entity_registry_enabled_default=False,

    # ),
]

AC_CHARGER_SELECTS = []
DC_CHARGER_SELECTS = []

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy select platform."""
    coordinator: SigenergyDataUpdateCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"])
    plant_name = config_entry.data[CONF_NAME]
    _LOGGER.debug("Starting to add %s", SigenergySelect)
    # Add plant Selects
    entities : list[SigenergySelect] = generate_sigen_entity(plant_name, None, None, coordinator,
                                                             SigenergySelect,
                                                             PLANT_SELECTS,
                                                             DEVICE_TYPE_PLANT)

    # Add inverter Selects
    for device_name, device_conn in coordinator.hub.inverter_connections.items():
        entities += generate_sigen_entity(plant_name, device_name, device_conn, coordinator,
                                          SigenergySelect,
                                          INVERTER_SELECTS,
                                          DEVICE_TYPE_INVERTER)

    # Add AC charger Selects
    for device_name, device_conn in coordinator.hub.ac_charger_connections.items():
        entities += generate_sigen_entity(plant_name, device_name, device_conn, coordinator,
                                          SigenergySelect,
                                          AC_CHARGER_SELECTS,
                                          DEVICE_TYPE_AC_CHARGER)

    async_add_entities(entities)
    return

class SigenergySelect(SigenergyEntity, SelectEntity):
    """Representation of a Sigenergy select."""

    entity_description: SigenergySelectEntityDescription
    # Explicitly type coordinator here
    coordinator: SigenergyDataUpdateCoordinator

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergySelectEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None, # Changed to Optional[str]
        device_name: Optional[str] = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the select."""
        # Call the base class __init__
        super().__init__(
            coordinator=coordinator,
            description=description,
            name=name,
            device_type=device_type,
            device_id=device_id,
            device_name=device_name,
            device_info=device_info,
            pv_string_idx=pv_string_idx,
        )

        # Select-specific initialization
        # Used by SelectEntity to determine valid choices.
        self._attr_options = description.options if description.options is not None else []

    @property
    def current_option(self) -> str:
        """Return the selected entity option."""
        if self.coordinator.data is None:
            return self.options[0] if self.options else ""

        # Use device_name as the primary identifier passed to the lambda/function
        identifier = self._device_name
        try:
            option = self.entity_description.current_option_fn(self.coordinator.data, identifier)
            return option if option is not None else ""
        except Exception as e:
            _LOGGER.error("Error getting current_option for %s (identifier: %s): %s",
                          self.entity_id, identifier, e)
            return ""

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Use device_name as the primary identifier passed to the lambda/function
        identifier = self._device_name
        # Exceptions are handled and logged in coordinator.async_write_parameter
        await self.entity_description.select_option_fn(self.coordinator, identifier, option)

    def select_option(self, option: str) -> None:
        """Change the selected option."""
        # This is the synchronous version of the method.
        # We are using an async function so we need to wrap this in a task
        # and wait for it to complete.
        asyncio.run_coroutine_threadsafe(
            self.async_select_option(option), self.hass.loop
        ).result()