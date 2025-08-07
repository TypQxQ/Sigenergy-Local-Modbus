"""Number platform for Sigenergy ESS integration."""
from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Coroutine

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry  #pylint: disable=no-name-in-module, syntax-error
from homeassistant.const import (
    CONF_NAME,
    EntityCategory,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_PLANT,
    DOMAIN,
)
from .coordinator import SigenergyDataUpdateCoordinator # Import coordinator
from .modbus import SigenergyModbusError
from .common import(generate_sigen_entity, generate_unique_entity_id, generate_device_id) # Added generate_device_id
from .sigen_entity import SigenergyEntity # Import the new base class

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SigenergyNumberEntityDescription(NumberEntityDescription):
    """Class describing Sigenergy number entities."""

    # Provide default lambdas instead of None to satisfy type checker
    # The second argument 'identifier' will be device_name for inverters, device_id otherwise
    value_fn: Callable[[Dict[str, Any], Optional[Any]], float] = lambda data, identifier: 0.0
    # Make set_value_fn async and update type hint
    # Make set_value_fn async and update type hint to accept coordinator
    set_value_fn: Callable[[SigenergyDataUpdateCoordinator, Optional[Any], float], Coroutine[Any, Any, None]] = lambda coordinator, identifier, value: asyncio.sleep(0) # Placeholder async lambda
    available_fn: Callable[[Dict[str, Any], Optional[Any]], bool] = lambda data, _: True
    entity_registry_enabled_default: bool = True


PLANT_NUMBERS = [
    SigenergyNumberEntityDescription(
        key="plant_active_power_fixed_target",
        name="Active Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_active_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_active_power_fixed_target", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_reactive_power_fixed_target",
        name="Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kvar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_reactive_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_reactive_power_fixed_target", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_active_power_percentage_target",
        name="Active Power Percentage Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_active_power_percentage_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_active_power_percentage_target", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_qs_ratio_target",
        name="Q/S Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_qs_ratio_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_qs_ratio_target", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_power_factor_target",
        name="Power Factor Adjustment",
        icon="mdi:sine-wave",
        native_min_value=-1,
        native_max_value=1,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_power_factor_target", 0) / 1000,
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_power_factor_target", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_ess_max_charging_limit",
        name="ESS Max Charging Limit",
        icon="mdi:battery-charging",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_ess_max_charging_limit", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_ess_max_charging_limit", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_ess_max_discharging_limit",
        name="ESS Max Discharging Limit",
        icon="mdi:battery-charging-outline",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_ess_max_discharging_limit", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_ess_max_discharging_limit", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_pv_max_power_limit",
        name="PV Max Power Limit",
        icon="mdi:solar-power",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_pv_max_power_limit", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_pv_max_power_limit", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_grid_point_maximum_export_limitation",
        name="Grid Export Limitation",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_grid_point_maximum_export_limitation", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_grid_point_maximum_export_limitation", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_grid_maximum_import_limitation",
        name="Grid Import Limitation",
        icon="mdi:transmission-tower-import",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_grid_maximum_import_limitation", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_grid_maximum_import_limitation", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_pcs_maximum_export_limitation",
        name="PCS Export Limitation",
        icon="mdi:transmission-tower-export",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_pcs_maximum_export_limitation", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_pcs_maximum_export_limitation", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_pcs_maximum_import_limitation",
        name="PCS Import Limitation",
        icon="mdi:transmission-tower-import",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=0,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_pcs_maximum_import_limitation", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_pcs_maximum_import_limitation", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_a_active_power_fixed_target",
        name="Phase A Active Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_a_active_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_a_active_power_fixed_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_b_active_power_fixed_target",
        name="Phase B Active Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_b_active_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_b_active_power_fixed_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_c_active_power_fixed_target",
        name="Phase C Active Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_c_active_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_c_active_power_fixed_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_a_reactive_power_fixed_target",
        name="Phase A Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kvar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_a_reactive_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_a_reactive_power_fixed_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_b_reactive_power_fixed_target",
        name="Phase B Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kvar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_b_reactive_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_b_reactive_power_fixed_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_c_reactive_power_fixed_target",
        name="Phase C Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kvar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_c_reactive_power_fixed_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_c_reactive_power_fixed_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_a_active_power_percentage_target",
        name="Phase A Active Power Percentage",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_a_active_power_percentage_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_a_active_power_percentage_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_b_active_power_percentage_target",
        name="Phase B Active Power Percentage",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_b_active_power_percentage_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_b_active_power_percentage_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_c_active_power_percentage_target",
        name="Phase C Active Power Percentage",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_c_active_power_percentage_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_c_active_power_percentage_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_a_qs_ratio_target",
        name="Phase A Q/S Ratio",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_a_qs_ratio_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_a_qs_ratio_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_b_qs_ratio_target",
        name="Phase B Q/S Ratio",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_b_qs_ratio_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_b_qs_ratio_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="plant_phase_c_qs_ratio_target",
        name="Phase C Q/S Ratio",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_phase_c_qs_ratio_target", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_phase_c_qs_ratio_target", value),
        available_fn=lambda data, _: data["plant"].get("plant_independent_phase_power_control_enable") == 1,
        entity_registry_enabled_default=False,
    ),
    # Additions for Modbus specification v2.7
        SigenergyNumberEntityDescription(
        key="plant_backup_soc",
        name="ESS Backup State of Charge",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_backup_soc", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_backup_soc", value),
        entity_registry_enabled_default=False,
    ),
        SigenergyNumberEntityDescription(
        key="plant_charge_cut_off_soc",
        name="ESS Charge Cut-Off State of Charge",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_charge_cut_off_soc", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_charge_cut_off_soc", value),
        entity_registry_enabled_default=False,
    ),
        SigenergyNumberEntityDescription(
        key="plant_discharge_cut_off_soc",
        name="ESS Discharge Cut-Off State of Charge",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data, _: data["plant"].get("plant_discharge_cut_off_soc", 0),
        set_value_fn=lambda coordinator, _, value: coordinator.async_write_parameter("plant", None, "plant_discharge_cut_off_soc", value),
        entity_registry_enabled_default=False,
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
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        # Use identifier (device_name for inverters)
        value_fn=lambda data, identifier: data["inverters"].get(identifier, {}).get("inverter_active_power_fixed_adjustment", 0),
        set_value_fn=lambda coordinator, identifier, value: coordinator.async_write_parameter("inverter", identifier, "inverter_active_power_fixed_adjustment", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="inverter_reactive_power_fixed_adjustment",
        name="Reactive Power Fixed Adjustment",
        icon="mdi:flash",
        native_unit_of_measurement="kvar",
        native_min_value=-100,
        native_max_value=100,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        # Use identifier (device_name for inverters)
        value_fn=lambda data, identifier: data["inverters"].get(identifier, {}).get("inverter_reactive_power_fixed_adjustment", 0),
        set_value_fn=lambda coordinator, identifier, value: coordinator.async_write_parameter("inverter", identifier, "inverter_reactive_power_fixed_adjustment", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="inverter_active_power_percentage_adjustment",
        name="Active Power Percentage Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-100,
        native_max_value=100,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        # Use identifier (device_name for inverters)
        value_fn=lambda data, identifier: data["inverters"].get(identifier, {}).get("inverter_active_power_percentage_adjustment", 0),
        set_value_fn=lambda coordinator, identifier, value: coordinator.async_write_parameter("inverter", identifier, "inverter_active_power_percentage_adjustment", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="inverter_reactive_power_qs_adjustment",
        name="Reactive Power Q/S Adjustment",
        icon="mdi:percent",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=-60,
        native_max_value=60,
        native_step=0.01,
        entity_category=EntityCategory.CONFIG,
        # Use identifier (device_name for inverters)
        value_fn=lambda data, identifier: data["inverters"].get(identifier, {}).get("inverter_reactive_power_qs_adjustment", 0),
        set_value_fn=lambda coordinator, identifier, value: coordinator.async_write_parameter("inverter", identifier, "inverter_reactive_power_qs_adjustment", value),
        entity_registry_enabled_default=False,
    ),
    SigenergyNumberEntityDescription(
        key="inverter_power_factor_adjustment",
        name="Power Factor Adjustment",
        icon="mdi:sine-wave",
        native_min_value=-1,
        native_max_value=1,
        native_step=0.001,
        entity_category=EntityCategory.CONFIG,
        # Use identifier (device_name for inverters)
        value_fn=lambda data, identifier: data["inverters"].get(identifier, {}).get("inverter_power_factor_adjustment", 0) / 1000,
        set_value_fn=lambda coordinator, identifier, value: coordinator.async_write_parameter("inverter", identifier, "inverter_power_factor_adjustment", value),
        entity_registry_enabled_default=False,
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
        # identifier here will be ac_charger_name
        value_fn=lambda data, identifier: data["ac_chargers"].get(identifier, {}).get("ac_charger_output_current", 0),
        set_value_fn=lambda coordinator, identifier, value: coordinator.async_write_parameter("ac_charger", identifier, "ac_charger_output_current", value),
        entity_registry_enabled_default=False,
    ),
]

DC_CHARGER_NUMBERS = []


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy number platform."""
    coordinator: SigenergyDataUpdateCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"])
    plant_name = config_entry.data[CONF_NAME]

    # Add plant numbers
    entities : list[SigenergyNumber] = generate_sigen_entity(plant_name, None, None, coordinator,
                                                             SigenergyNumber,
                                                             PLANT_NUMBERS,
                                                             DEVICE_TYPE_PLANT)

    # Add inverter numbers
    for device_name, device_conn in coordinator.hub.inverter_connections.items():
        entities += generate_sigen_entity(plant_name, device_name, device_conn, coordinator,
                                           SigenergyNumber,
                                           INVERTER_NUMBERS,
                                           DEVICE_TYPE_INVERTER)

    # Add AC charger numbers
    for device_name, device_conn in coordinator.hub.ac_charger_connections.items():
        entities += generate_sigen_entity(plant_name, device_name, device_conn, coordinator,
                                           SigenergyNumber,
                                           AC_CHARGER_NUMBERS,
                                           DEVICE_TYPE_AC_CHARGER)

    async_add_entities(entities)


class SigenergyNumber(SigenergyEntity, NumberEntity):
    """Representation of a Sigenergy number."""

    entity_description: SigenergyNumberEntityDescription
    # Explicitly type coordinator here
    coordinator: SigenergyDataUpdateCoordinator

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergyNumberEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None, # Changed to Optional[str]
        device_name: Optional[str] = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the number."""
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
        # No number-specific init needed for now

    @property
    def native_value(self) -> float:
        """Return the value of the number."""
        if self.coordinator.data is None:
            return 0.0 # Return float default
            
        # Use device_name as the primary identifier passed to the lambda/function
        identifier = self._device_name
        try:
            value = self.entity_description.value_fn(self.coordinator.data, identifier)
            # Ensure the value is a float
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError, KeyError) as e:
            _LOGGER.error(f"Error getting native value for {self.entity_id} (identifier: {identifier}): {e}")
            return 0.0


    # The 'available' property is now inherited from SigenergyEntity
    # We might need to override it here if the available_fn logic needs specific handling
    # for number entities, but for now, let's rely on the base implementation.

    async def async_set_native_value(self, value: float) -> None:
        """Set the value of the number."""
        # Use device_name as the primary identifier passed to the lambda/function
        identifier = self._device_name
        # Exceptions are handled and logged in coordinator.async_write_parameter
        await self.entity_description.set_value_fn(self.coordinator, identifier, value)