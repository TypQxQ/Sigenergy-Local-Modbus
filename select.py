"""Select platform for Sigenergy ESS integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import SigenergyEntity, setup_entities
from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
    EMSWorkMode,
    RemoteEMSControlMode,
)
from .modbus import SigenergyModbusError

_LOGGER = logging.getLogger(__name__)


@dataclass
class SigenergySelectEntityDescription(SelectEntityDescription):
    """Class describing Sigenergy select entities."""

    current_option_fn: Callable[[Dict[str, Any], Optional[int]], str] = None
    select_option_fn: Callable[[Any, Optional[int], str], None] = None
    available_fn: Callable[[Dict[str, Any], Optional[int]], bool] = lambda data, _: True
    entity_registry_enabled_default: bool = True


PLANT_SELECTS = [
    SigenergySelectEntityDescription(
        key="ems_work_mode",
        name="EMS Work Mode",
        icon="mdi:home-battery",
        options=[
            "Maximum Self Consumption",
            "AI Mode",
            "Time of Use",
            "Remote EMS",
        ],
        entity_category=EntityCategory.CONFIG,
        current_option_fn=lambda data, _: {
            EMSWorkMode.MAX_SELF_CONSUMPTION: "Maximum Self Consumption",
            EMSWorkMode.AI_MODE: "AI Mode",
            EMSWorkMode.TOU: "Time of Use",
            EMSWorkMode.REMOTE_EMS: "Remote EMS",
        }.get(data["plant"].get("ems_work_mode"), "Unknown"),
        select_option_fn=lambda hub, _, option: hub.async_write_plant_parameter(
            "ems_work_mode",
            {
                "Maximum Self Consumption": EMSWorkMode.MAX_SELF_CONSUMPTION,
                "AI Mode": EMSWorkMode.AI_MODE,
                "Time of Use": EMSWorkMode.TOU,
                "Remote EMS": EMSWorkMode.REMOTE_EMS,
            }.get(option, EMSWorkMode.MAX_SELF_CONSUMPTION),
        ),
    ),
    SigenergySelectEntityDescription(
        key="remote_ems_control_mode",
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
        ],
        entity_category=EntityCategory.CONFIG,
        current_option_fn=lambda data, _: {
            RemoteEMSControlMode.PCS_REMOTE_CONTROL: "PCS Remote Control",
            RemoteEMSControlMode.STANDBY: "Standby",
            RemoteEMSControlMode.MAXIMUM_SELF_CONSUMPTION: "Maximum Self Consumption",
            RemoteEMSControlMode.COMMAND_CHARGING_GRID_FIRST: "Command Charging (Grid First)",
            RemoteEMSControlMode.COMMAND_CHARGING_PV_FIRST: "Command Charging (PV First)",
            RemoteEMSControlMode.COMMAND_DISCHARGING_PV_FIRST: "Command Discharging (PV First)",
            RemoteEMSControlMode.COMMAND_DISCHARGING_ESS_FIRST: "Command Discharging (ESS First)",
        }.get(data["plant"].get("remote_ems_control_mode"), "Unknown"),
        select_option_fn=lambda hub, _, option: hub.async_write_plant_parameter(
            "remote_ems_control_mode",
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
        available_fn=lambda data, _: data["plant"].get("remote_ems_enable") == 1,
    ),
]

AC_CHARGER_SELECTS = [
    SigenergySelectEntityDescription(
        key="ac_charger_mode",
        name="DC Charger Mode",
        icon="mdi:ev-station",
        options=[
            "Auto",
            "Manual",
        ],
        entity_category=EntityCategory.CONFIG,
        current_option_fn=lambda data, ac_charger_id: {
            0: "Auto",
            1: "Manual",
        }.get(data["ac_chargers"].get(ac_charger_id, {}).get("ac_charger_mode", 0), "Unknown"),
        select_option_fn=lambda hub, ac_charger_id, option: hub.async_write_ac_charger_parameter(
            ac_charger_id,
            "ac_charger_mode",
            {
                "Auto": 0,
                "Manual": 1,
            }.get(option, 0),
        ),
    ),
]

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy select platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][config_entry.entry_id]["hub"]
    entities = []

    # Create plant selects
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            PLANT_SELECTS,
            SigenergySelect,
            DEVICE_TYPE_PLANT,
            hub=hub
        )
    )

    # Create AC charger selects
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            AC_CHARGER_SELECTS,
            SigenergySelect,
            DEVICE_TYPE_AC_CHARGER,
            coordinator.hub.ac_charger_slave_ids,
            hub=hub
        )
    )

    async_add_entities(entities)


class SigenergySelect(SigenergyEntity, SelectEntity):
    """Representation of a Sigenergy select."""

    entity_description: SigenergySelectEntityDescription

    def __init__(
        self,
        coordinator,
        hub: Any,
        description: SigenergySelectEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[int],
        device_name: Optional[str] = "",
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator, name, device_type, device_id, device_name)
        self.entity_description = description
        self.hub = hub
        self._attr_options = description.options
        # Complete unique ID with description key
        self._attr_unique_id = f"{self._attr_unique_id}_{description.key}"

    @property
    def current_option(self) -> str:
        """Return the selected entity option."""
        if self.coordinator.data is None:
            return self.options[0] if self.options else ""
            
        return self.entity_description.current_option_fn(self.coordinator.data, self._device_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
            
        # Check if the entity has a specific availability function
        if hasattr(self.entity_description, "available_fn"):
            return self.entity_description.available_fn(self.coordinator.data, self._device_id)
                
        return True

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            await self.entity_description.select_option_fn(self.hub, self._device_id, option)
            await self.coordinator.async_request_refresh()
        except SigenergyModbusError as error:
            _LOGGER.error("Failed to select option %s for %s: %s", option, self.name, error)