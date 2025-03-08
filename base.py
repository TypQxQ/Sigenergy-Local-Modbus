"""Base classes for Sigenergy integration entities."""
from __future__ import annotations

from typing import Any, Optional

from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
)
from .coordinator import SigenergyDataUpdateCoordinator


class SigenergyEntity(CoordinatorEntity):
    """Base class for Sigenergy entities."""

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        name: str,
        device_type: str,
        device_id: Optional[int],
        device_name: Optional[str] = "",
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_name = name
        self._device_type = device_type
        self._device_id = device_id
        self._device_name = device_name

        # Get the device number if any as a string for use in names
        device_number_str = device_name.split()[-1] if device_name else ""
        device_number_str = f" {device_number_str}" if device_number_str.isdigit() else ""

        # Set unique ID
        if device_type == DEVICE_TYPE_PLANT:
            self._attr_unique_id = f"{coordinator.hub.config_entry.entry_id}_{device_type}"
        else:
            self._attr_unique_id = f"{coordinator.hub.config_entry.entry_id}_{device_type}_{device_number_str}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this device."""
        if self._device_type == DEVICE_TYPE_PLANT:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_plant")},
                name=self._device_name,
                manufacturer="Sigenergy",
                model="Energy Storage System",
                via_device=(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_plant"),
            )
        elif self._device_type == DEVICE_TYPE_INVERTER:
            # Get model and serial number if available
            model = None
            serial_number = None
            if self.coordinator.data and "inverters" in self.coordinator.data:
                inverter_data = self.coordinator.data["inverters"].get(self._device_id, {})
                model = inverter_data.get("model_type")
                serial_number = inverter_data.get("serial_number")

            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_{str(self._device_name).lower().replace(' ', '_')}")},
                name=self._device_name,
                manufacturer="Sigenergy",
                model=model,
                serial_number=serial_number,
                via_device=(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_plant"),
            )
        elif self._device_type == DEVICE_TYPE_AC_CHARGER:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_{str(self._device_name).lower().replace(' ', '_')}")},
                name=self._device_name,
                manufacturer="Sigenergy",
                model="AC Charger",
                via_device=(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_plant"),
            )
        elif self._device_type == DEVICE_TYPE_DC_CHARGER:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_{str(self._device_name).lower().replace(' ', '_')}")},
                name=self._device_name,
                manufacturer="Sigenergy",
                model="DC Charger",
                via_device=(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_plant"),
            )
        
        # Default fallback
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.hub.config_entry.entry_id}_{self._device_type}")},
            name=self._device_name,
            manufacturer="Sigenergy",
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False
            
        if self._device_type == DEVICE_TYPE_PLANT:
            return self.coordinator.data is not None and "plant" in self.coordinator.data
        elif self._device_type == DEVICE_TYPE_INVERTER:
            return (
                self.coordinator.data is not None
                and "inverters" in self.coordinator.data
                and self._device_id in self.coordinator.data["inverters"]
            )
        elif self._device_type == DEVICE_TYPE_AC_CHARGER:
            return (
                self.coordinator.data is not None
                and "ac_chargers" in self.coordinator.data
                and self._device_id in self.coordinator.data["ac_chargers"]
            )
        elif self._device_type == DEVICE_TYPE_DC_CHARGER:
            return (
                self.coordinator.data is not None
                and "dc_chargers" in self.coordinator.data
                and self._device_id in self.coordinator.data["dc_chargers"]
            )
            
        return False

    @staticmethod
    def create_entity_id_suffix(description_key: str) -> str:
        """Create entity ID suffix from description key."""
        return f"_{description_key}"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


def create_device_name(plant_name: str, device_type: str, device_no: int) -> str:
    """Create standardized device name."""
    device_type_name = {
        DEVICE_TYPE_PLANT: "",
        DEVICE_TYPE_INVERTER: "Inverter",
        DEVICE_TYPE_AC_CHARGER: "AC Charger",
        DEVICE_TYPE_DC_CHARGER: "DC Charger",
    }.get(device_type, "")
    
    # If plant name ends with a number, include it in the device name
    plant_suffix = f"{plant_name.split()[-1]} " if plant_name.split()[-1].isdigit() else ""
    
    # For plant, just return the plant name
    if device_type == DEVICE_TYPE_PLANT:
        return plant_name
        
    # For other device types, create a standardized name
    device_suffix = "" if device_no == 0 else f" {device_no}"
    return f"Sigen {plant_suffix}{device_type_name}{device_suffix}"


def setup_entities(
    config_entry, 
    coordinator, 
    descriptions_list, 
    entity_class, 
    device_type, 
    device_ids=None, 
    additional_args=None
):
    """Set up entities for a specific device type."""
    entities = []
    plant_name = config_entry.data[CONF_NAME]
    
    if device_type == DEVICE_TYPE_PLANT:
        # Handle plant entities
        device_name = plant_name
        for description in descriptions_list:
            entities.append(
                entity_class(
                    coordinator=coordinator,
                    **(additional_args or {}),
                    description=description,
                    name=f"{plant_name} {description.name}",
                    device_type=device_type,
                    device_id=None,
                    device_name=device_name,
                )
            )
    else:
        # Handle other device types
        device_no = 0
        for device_id in device_ids:
            device_name = create_device_name(plant_name, device_type, device_no)
            for description in descriptions_list:
                entities.append(
                    entity_class(
                        coordinator=coordinator,
                        **(additional_args or {}),
                        description=description,
                        name=f"{device_name} {description.name}",
                        device_type=device_type,
                        device_id=device_id,
                        device_name=device_name,
                    )
                )
            device_no += 1
    
    return entities