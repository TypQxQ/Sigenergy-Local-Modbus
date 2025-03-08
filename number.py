"""Number platform for Sigenergy ESS integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    EntityCategory,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfPower,
)
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
from .modbus import SigenergyModbusError

_LOGGER = logging.getLogger(__name__)


@dataclass
class SigenergyNumberEntityDescription(NumberEntityDescription):
    """Class describing Sigenergy number entities."""

    value_fn: Callable[[Dict[str, Any], Optional[int]], float] = None
    set_value_fn: Callable[[Any, Optional[int], float], None] = None
    available_fn: Callable[[Dict[str, Any], Optional[int]], bool] = lambda data, _: True
    entity_registry_enabled_default: bool = True


PLANT_NUMBERS = [
    SigenergyNumberEntityDescription(
        key="plant_active_power_fixed_target",
        name="Active Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_active_power_fixed_target", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_active_power_fixed_target", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_reactive_power_fixed_target",
        name="Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kVar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_reactive_power_fixed_target", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_reactive_power_fixed_target", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_active_power_percentage_target",
        name="Active Power Percentage Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_active_power_percentage_target", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_active_power_percentage_target", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_qs_ratio_target",
        name="Q/S Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_qs_ratio_target", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_qs_ratio_target", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_power_factor_target",
        name="Power Factor Adjustment",
        icon="mdi:sine-wave",
        native_min_value=-1,
        native_max_value=1,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_power_factor_target", 0) / 1000,
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_power_factor_target", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_ess_max_charging_limit",
        name="ESS Max Charging Limit",
        icon="mdi:battery-charging",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,  # This will be adjusted dynamically based on rated power
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_ess_max_charging_limit", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_ess_max_charging_limit", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_ess_max_discharging_limit",
        name="ESS Max Discharging Limit",
        icon="mdi:battery-charging-outline",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,  # This will be adjusted dynamically based on rated power
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_ess_max_discharging_limit", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_ess_max_discharging_limit", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_pv_max_power_limit",
        name="PV Max Power Limit",
        icon="mdi:solar-power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,  # This will be adjusted dynamically based on rated power
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_pv_max_power_limit", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_pv_max_power_limit", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_grid_point_maximum_export_limitation",
        name="Grid Export Limitation",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,  # This will be adjusted dynamically based on rated power
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_grid_point_maximum_export_limitation", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_grid_point_maximum_export_limitation", value),
    ),
    SigenergyNumberEntityDescription(
        key="plant_grid_maximum_import_limitation",
        name="Grid Import Limitation",
        icon="mdi:transmission-tower-import",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,  # This will be adjusted dynamically based on rated power
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_grid_maximum_import_limitation", 0),
        set_value_fn=lambda hub, _, value: hub.async_write_plant_parameter("plant_grid_maximum_import_limitation", value),
    ),
]

INVERTER_NUMBERS = [
    SigenergyNumberEntityDescription(
        key="inverter_active_power_fixed_adjustment",
        name="Active Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_active_power_fixed_adjustment", 0),
        set_value_fn=lambda hub, inverter_id, value: hub.async_write_inverter_parameter(inverter_id, "inverter_active_power_fixed_adjustment", value),
    ),
    SigenergyNumberEntityDescription(
        key="inverter_reactive_power_fixed_adjustment",
        name="Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kVar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_reactive_power_fixed_adjustment", 0),
        set_value_fn=lambda hub, inverter_id, value: hub.async_write_inverter_parameter(inverter_id, "inverter_reactive_power_fixed_adjustment", value),
    ),
    SigenergyNumberEntityDescription(
        key="inverter_active_power_percentage_adjustment",
        name="Active Power Percentage Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_active_power_percentage_adjustment", 0),
        set_value_fn=lambda hub, inverter_id, value: hub.async_write_inverter_parameter(inverter_id, "inverter_active_power_percentage_adjustment", value),
    ),
    SigenergyNumberEntityDescription(
        key="inverter_reactive_power_qs_adjustment",
        name="Reactive Power Q/S Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_reactive_power_qs_adjustment", 0),
        set_value_fn=lambda hub, inverter_id, value: hub.async_write_inverter_parameter(inverter_id, "inverter_reactive_power_qs_adjustment", value),
    ),
    SigenergyNumberEntityDescription(
        key="inverter_power_factor_adjustment",
        name="Power Factor Adjustment",
        icon="mdi:sine-wave",
        native_min_value=-1,
        native_max_value=1,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, inverter_id: data["inverters"].get(inverter_id, {}).get("inverter_power_factor_adjustment", 0) / 1000,
        set_value_fn=lambda hub, inverter_id, value: hub.async_write_inverter_parameter(inverter_id, "inverter_power_factor_adjustment", value),
    ),
]
AC_CHARGER_NUMBERS = [
    SigenergyNumberEntityDescription(
        key="ac_charger_output_current",
        name="Charger Output Current",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_min_value=6,
        native_max_value=32,  # This will be adjusted dynamically based on rated current
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, ac_charger_id: data["ac_chargers"].get(ac_charger_id, {}).get("ac_charger_output_current", 0),
        set_value_fn=lambda hub, ac_charger_id, value: hub.async_write_ac_charger_parameter(ac_charger_id, "ac_charger_output_current", value),
    ),
]

DC_CHARGER_NUMBERS = []



async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy number platform."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    hub = hass.data[DOMAIN][config_entry.entry_id]["hub"]
    entities = []

    # Add plant numbers
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            PLANT_NUMBERS,
            SigenergyNumber,
            DEVICE_TYPE_PLANT,
            hub=hub
        )
    )

    # Add inverter numbers
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            INVERTER_NUMBERS,
            SigenergyNumber,
            DEVICE_TYPE_INVERTER,
            coordinator.hub.inverter_slave_ids,
            hub=hub
        )
    )

    # Add AC charger numbers
    entities.extend(
        setup_entities(
            config_entry,
            coordinator,
            AC_CHARGER_NUMBERS,
            SigenergyNumber,
            DEVICE_TYPE_AC_CHARGER,
            coordinator.hub.ac_charger_slave_ids,
            hub=hub
        )
    )

    # Add DC charger numbers if any in the future
    if DC_CHARGER_NUMBERS and coordinator.hub.dc_charger_slave_ids:
        entities.extend(
            setup_entities(
                config_entry,
                coordinator,
                DC_CHARGER_NUMBERS,
                SigenergyNumber,
                DEVICE_TYPE_DC_CHARGER,
                coordinator.hub.dc_charger_slave_ids,
                hub=hub
            )
        )

    async_add_entities(entities)


class SigenergyNumber(SigenergyEntity, NumberEntity):
    """Representation of a Sigenergy number."""

    entity_description: SigenergyNumberEntityDescription

    def __init__(
        self,
        coordinator,
        hub: Any,
        description: SigenergyNumberEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[int],
        device_name: Optional[str] = "",
    ) -> None:
        """Initialize the number."""
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
    def native_value(self) -> float:
        """Return the value of the number."""
        if self.coordinator.data is None:
            return 0
            
        return self.entity_description.value_fn(self.coordinator.data, self._device_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
            
        # Check if the entity has a specific availability function
        if hasattr(self.entity_description, "available_fn"):
            return self.entity_description.available_fn(self.coordinator.data, self._device_id)
                
        return True

    async def async_set_native_value(self, value: float) -> None:
        """Set the value of the number."""
        try:
            await self.entity_description.set_value_fn(self.hub, self._device_id, value)
            await self.coordinator.async_request_refresh()
        except SigenergyModbusError as error:
            _LOGGER.error("Failed to set value %s for %s: %s", value, self.name, error)