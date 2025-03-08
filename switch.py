"""Switch platform for Sigenergy ESS integration."""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import SigenergyEntity, setup_entities
from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
)
from .coordinator import SigenergyDataUpdateCoordinator
from .modbus import SigenergyModbusError

_LOGGER = logging.getLogger(__name__)


@dataclass
class SigenergySwitchEntityDescription(SwitchEntityDescription):
    """Class describing Sigenergy switch entities."""

    is_on_fn: Callable[[Dict[str, Any], Optional[int]], bool] = None
    turn_on_fn: Callable[[Any, Optional[int]], None] = None
    turn_off_fn: Callable[[Any, Optional[int]], None] = None
    available_fn: Callable[[Dict[str, Any], Optional[int]], bool] = lambda data, _: True
    entity_registry_enabled_default: bool = True


PLANT_SWITCHES = [
    SigenergySwitchEntityDescription(
        key="plant_start_stop",
        name="Plant Power",
        icon="mdi:power",
        is_on_fn=lambda data, _: data["plant"].get("plant_running_state") == 1,
        turn_on_fn=lambda hub, _: hub.async_write_plant_parameter("plant_start_stop", 1),
        turn_off_fn=lambda hub, _: hub.async_write_plant_parameter("plant_start_stop", 0),
    ),
    SigenergySwitchEntityDescription(
        key="plant_remote_ems_enable",
        name="Remote EMS",
        icon="mdi:remote",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda data, _: data["plant"].get("plant_remote_ems_enable") == 1,
        turn_on_fn=lambda hub, _: hub.async_write_plant_parameter("plant_remote_ems_enable", 1),
        turn_off_fn=lambda hub, _: hub.async_write_plant_parameter("plant_remote_ems_enable", 0),
    ),
    SigenergySwitchEntityDescription(
        key="plant_independent_phase_power_control_enable",
        name="Independent Phase Power Control",
        icon="mdi:tune",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        turn_on_fn=lambda hub, _: hub.async_write_plant_parameter("plant_independent_phase_power_control_enable", 1),
        turn_off_fn=lambda hub, _: hub.async_write_plant_parameter("plant_independent_phase_power_control_enable", 0),
    ),
]

INVERTER_SWITCHES = [
    SigenergySwitchEntityDescription(
        key="inverter_start_stop",
        name="Inverter Power",
        icon="mdi:power",
        is_on_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_running_state") == 1,
        turn_on_fn=lambda hub, inverter_id: hub.async_write_inverter_parameter(inverter_id, "inverter_start_stop", 1),
        turn_off_fn=lambda hub, inverter_id: hub.async_write_inverter_parameter(inverter_id, "inverter_start_stop", 0),
    ),
    SigenergySwitchEntityDescription(
        key="inverter_remote_ems_dispatch_enable",
        name="Remote EMS Dispatch",
        icon="mdi:remote",
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_remote_ems_dispatch_enable") == 1,
        turn_on_fn=lambda hub, inverter_id: hub.async_write_inverter_parameter(inverter_id, "inverter_remote_ems_dispatch_enable", 1),
        turn_off_fn=lambda hub, inverter_id: hub.async_write_inverter_parameter(inverter_id, "inverter_remote_ems_dispatch_enable", 0),
    ),
]
AC_CHARGER_SWITCHES = [
    SigenergySwitchEntityDescription(
        key="ac_charger_start_stop",
        name="AC Charger Power",
        icon="mdi:ev-station",
        is_on_fn=lambda data, ac_charger_id: data["ac_chargers"].get(ac_charger_id, {}).get("ac_charger_system_state") > 0,
        turn_on_fn=lambda hub, ac_charger_id: hub.async_write_ac_charger_parameter(ac_charger_id, "ac_charger_start_stop", 0),
        turn_off_fn=lambda hub, ac_charger_id: hub.async_write_ac_charger_parameter(ac_charger_id, "ac_charger_start_stop", 1),
    ),
]

DC_CHARGER_SWITCHES = [
    SigenergySwitchEntityDescription(
        key="dc_charger_start_stop",
        name="DC Charger",
        icon="mdi:ev-station",
        # consider changing is_on_fn to check for dc_charger_output_power > 0 if the below doesn't work
        is_on_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("dc_charger_start_stop") == 0,
        turn_on_fn=lambda hub, inverter_id: hub.async_write_inverter_parameter(inverter_id, "dc_charger_start_stop", 0),
        turn_off_fn=lambda hub, inverter_id: hub.async_write_inverter_parameter(inverter_id, "dc_charger_start_stop", 1),
    ),
]



async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy switch platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][config_entry.entry_id]["hub"]
    entities = []

    # Add plant switches
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            PLANT_SWITCHES,
            SigenergySwitch,
            DEVICE_TYPE_PLANT,
            hub=hub
        )
    )

    # Add inverter switches
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            INVERTER_SWITCHES,
            SigenergySwitch,
            DEVICE_TYPE_INVERTER,
            coordinator.hub.inverter_slave_ids,
            hub=hub
        )
    )

    # Add AC charger switches
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            AC_CHARGER_SWITCHES,
            SigenergySwitch,
            DEVICE_TYPE_AC_CHARGER,
            coordinator.hub.ac_charger_slave_ids,
            hub=hub
        )
    )

    # Add DC charger switches
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            DC_CHARGER_SWITCHES,
            SigenergySwitch,
            DEVICE_TYPE_DC_CHARGER,
            coordinator.hub.dc_charger_slave_ids,
            hub=hub
        )
    )

    async_add_entities(entities)


class SigenergySwitch(SigenergyEntity, SwitchEntity):
    """Representation of a Sigenergy switch."""

    entity_description: SigenergySwitchEntityDescription

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        hub: Any,
        description: SigenergySwitchEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[int],
        device_name: Optional[str] = "",
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, name, device_type, device_id, device_name)
        self.entity_description = description
        self.hub = hub
        self._attr_name = name
        self._device_type = device_type
        self._device_id = device_id
        
        # Get the device number if any as a string for use in names
        device_number_str = device_name.split()[-1]
        device_number_str = f" {device_number_str}" if device_number_str.isdigit() else ""

        # Set unique ID
        if device_type == DEVICE_TYPE_PLANT:
            # self._attr_unique_id = f"{coordinator.hub.host}_{device_type}_{description.key}"
            self._attr_unique_id = f"{coordinator.hub.config_entry.entry_id}_{device_type}_{description.key}"
        else:
            # self._attr_unique_id = f"{coordinator.hub.host}_{device_type}_{device_id}_{description.key}"
            # Used for testing in development to allow multiple sensors with the same unique ID
            self._attr_unique_id = f"{coordinator.hub.config_entry.entry_id}_{device_type}_{device_number_str}_{description.key}"
        
        # Set device info
        if device_type == DEVICE_TYPE_PLANT:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_plant")},
                name=device_name,
                manufacturer="Sigenergy",
                model="Energy Storage System",
                # via_device=(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_plant"),
            )
        elif device_type == DEVICE_TYPE_INVERTER:
            # Get model and serial number if available
            model = None
            serial_number = None
            sw_version = None
            if coordinator.data and "inverters" in coordinator.data:
                inverter_data = coordinator.data["inverters"].get(device_id, {})
                model = inverter_data.get("inverter_model_type")
                serial_number = inverter_data.get("inverter_serial_number")
                sw_version = inverter_data.get("inverter_machine_firmware_version")

            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_{str(device_name).lower().replace(' ', '_')}")},
                name=device_name,
                manufacturer="Sigenergy",
                model=model,
                serial_number=serial_number,
                sw_version=sw_version,
                via_device=(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_plant"),
            )
        elif device_type == DEVICE_TYPE_AC_CHARGER:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_{str(device_name).lower().replace(' ', '_')}")},
                name=device_name,
                manufacturer="Sigenergy",
                model="AC Charger",
                via_device=(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_plant"),
            )
        elif device_type == DEVICE_TYPE_DC_CHARGER:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_{str(device_name).lower().replace(' ', '_')}")},
                name=device_name,
                manufacturer="Sigenergy",
                model="DC Charger",
                via_device=(DOMAIN, f"{coordinator.hub.config_entry.entry_id}_plant"),
            )

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        if self.coordinator.data is None:
            return False
            
        return self.entity_description.is_on_fn(self.coordinator.data, self._device_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            await self.entity_description.turn_on_fn(self.hub, self._device_id)
            await self.coordinator.async_request_refresh()
        except SigenergyModbusError as error:
            _LOGGER.error("Failed to turn on %s: %s", self.name, error)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            await self.entity_description.turn_off_fn(self.hub, self._device_id)
            await self.coordinator.async_request_refresh()
        except SigenergyModbusError as error:
            _LOGGER.error("Failed to turn off %s: %s", self.name, error)