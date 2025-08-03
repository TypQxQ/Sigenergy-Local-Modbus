"""Sensor platform for Sigenergy ESS integration."""

from __future__ import annotations
import logging
from typing import Any, Optional, cast
from decimal import Decimal, InvalidOperation

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import (  # pylint: disable=syntax-error
    ConfigEntry,
)
from homeassistant.const import (
    CONF_NAME,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .modbusregisterdefinitions import (
    RunningState,
    ALARM_CODES,
    UpdateFrequencyType,
)
from .coordinator import SigenergyDataUpdateCoordinator
from .calculated_sensor import (
    SigenergyCalculations as SC,
    SigenergyCalculatedSensors as SCS,
    SigenergyIntegrationSensor,
)
from .static_sensor import StaticSensors as SS
from .static_sensor import COORDINATOR_DIAGNOSTIC_SENSORS # Import the new descriptions
from .common import generate_sigen_entity, generate_device_id, SigenergySensorEntityDescription, SensorEntityDescription
from .const import (
    DOMAIN,
    DEVICE_TYPE_PLANT,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    CONF_SLAVE_ID,
    CONF_INVERTER_HAS_DCCHARGER,
)
from .sigen_entity import SigenergyEntity # Import the new base class

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy sensor platform."""
    coordinator: SigenergyDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id]["coordinator"]
    plant_name = config_entry.data[CONF_NAME]

    entities: list[SigenergySensor] = []

    # Add plant sensors
    # Static Sensors:
    async_add_entities(
        generate_sigen_entity(
            plant_name,
            None,
            None,
            coordinator,
            SigenergySensor,
            SS.PLANT_SENSORS,
            DEVICE_TYPE_PLANT,
        )
    )
    # Calculated Sensors:
    async_add_entities(
        generate_sigen_entity(
            plant_name,
            None,
            None,
            coordinator,
            SigenergySensor,
            SCS.PLANT_SENSORS,
            DEVICE_TYPE_PLANT,
        )
    )

    # Calculated Integration Sensors:
    async_add_entities(
        generate_sigen_entity(
            plant_name,
            None,
            None,
            coordinator,
            SigenergyIntegrationSensor,
            SCS.PLANT_INTEGRATION_SENSORS,
            DEVICE_TYPE_PLANT,
            hass,
        )
    )

    # Add Coordinator Diagnostic Sensors:
    async_add_entities(
        generate_sigen_entity(
            plant_name,
            None, # No specific device name needed for plant-level coordinator sensor
            None, # No specific connection details needed
            coordinator,
            CoordinatorDiagnosticSensor, # Use the new class
            list(COORDINATOR_DIAGNOSTIC_SENSORS), # Use the new descriptions (converted to list)
            DEVICE_TYPE_PLANT, # Associate with the plant device
            # hass=hass, # Only needed if generate_sigen_entity requires it
        )
    )


    # Add inverter sensors
    for device_name, device_conn in coordinator.hub.inverter_connections.items():
        # Static Sensors:
        async_add_entities(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                SigenergySensor,
                SS.INVERTER_SENSORS,
                DEVICE_TYPE_INVERTER,
            )
        )
        # Calculated Sensors:
        async_add_entities(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                SigenergySensor,
                SCS.INVERTER_SENSORS,
                DEVICE_TYPE_INVERTER,
            )
        )

        # Calculated Integration Sensors:
        async_add_entities(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                SigenergyIntegrationSensor,
                SCS.INVERTER_INTEGRATION_SENSORS,
                DEVICE_TYPE_INVERTER,
                hass,
            )
        )

        # PV strings
        inverter_data = None
        if (
            coordinator.data is not None
            and "inverters" in coordinator.data
            and device_name in coordinator.data["inverters"]
        ):
            inverter_data = coordinator.data["inverters"][device_name]
        else:
            _LOGGER.warning(
                "No inverter data found for device '%s'. Skipping PV string sensor creation.",
                device_name,
            )
            continue

        pv_string_count = inverter_data.get("inverter_pv_string_count", 0)

        if (
            pv_string_count
            and isinstance(pv_string_count, (int, float))
            and pv_string_count > 0
        ):

            # Create sensors for each PV string
            for pv_idx in range(1, int(pv_string_count) + 1):
                try:
                    pv_string_name = f"{device_name} PV{pv_idx}"
                    parent_inverter_id = f"{coordinator.hub.config_entry.entry_id}_{generate_device_id(device_name)}"
                    pv_string_id = f"{parent_inverter_id}_pv{pv_idx}"

                    # Create device info
                    pv_device_info = DeviceInfo(
                        identifiers={(DOMAIN, pv_string_id)},
                        name=pv_string_name,
                        manufacturer="Sigenergy",
                        model="PV String",
                        via_device=(DOMAIN, parent_inverter_id),
                    )

                    # For this PV string add first the static and then the calculated sensors
                    async_add_entities(
                        generate_sigen_entity(
                            plant_name,
                            device_name,
                            device_conn,
                            coordinator,
                            PVStringSensor,
                            SS.PV_STRING_SENSORS,
                            DEVICE_TYPE_INVERTER,
                            hass=hass,
                            device_info=pv_device_info,
                            pv_string_idx=pv_idx,
                        )
                    )

                    async_add_entities(
                        generate_sigen_entity(
                            plant_name,
                            device_name,
                            device_conn,
                            coordinator,
                            PVStringSensor,
                            SCS.PV_STRING_SENSORS,
                            DEVICE_TYPE_INVERTER,
                            hass=hass,
                            device_info=pv_device_info,
                            pv_string_idx=pv_idx,
                        )
                    )

                    # Add PV String Integration Sensors
                    async_add_entities(
                        generate_sigen_entity(
                            plant_name,
                            device_name,
                            device_conn,
                            coordinator,
                            SigenergyIntegrationSensor, # Use the integration sensor class
                            SCS.PV_INTEGRATION_SENSORS, # Use the PV integration descriptions
                            DEVICE_TYPE_INVERTER, # Still associated with the inverter device type contextually
                            hass=hass,
                            device_info=pv_device_info, # Use the specific PV string device info
                            pv_string_idx=pv_idx,
                        )
                    )

                except Exception as ex:
                    _LOGGER.exception(
                        "Error creating device/sensors for PV string %d: %s", pv_idx, ex
                    )  # Use .exception to include traceback

        # Add DC charger sensors
        if device_conn.get(CONF_INVERTER_HAS_DCCHARGER, False):

            dc_name = f"{device_name} DC Charger"
            parent_inverter_id = f"{coordinator.hub.config_entry.entry_id}_{generate_device_id(device_name)}"
            dc_id = f"{parent_inverter_id}_dc_charger"

            # Create device info
            dc_device_info = DeviceInfo(
                identifiers={(DOMAIN, dc_id)},
                name=dc_name,
                manufacturer="Sigenergy",
                model="DC Charger",
                via_device=(DOMAIN, parent_inverter_id),
            )


            # Static Sensors:
            async_add_entities(
                generate_sigen_entity(
                    plant_name,
                    device_name,
                    device_conn,
                    coordinator,
                    SigenergySensor,
                    SS.DC_CHARGER_SENSORS,
                    DEVICE_TYPE_DC_CHARGER,
                    device_info=dc_device_info
                )
            )

    # Add AC charger sensors
    # Iterate through the AC charger connection details dictionary
    for ac_charger_name, ac_details in coordinator.hub.ac_charger_connections.items():
        slave_id = ac_details.get(CONF_SLAVE_ID)
        if slave_id is None:
            _LOGGER.warning(
                "Missing slave ID for AC charger '%s' in configuration, skipping sensor setup.",
                ac_charger_name,
            )
            continue  # Skip this charger if slave_id is missing

        for description in SS.AC_CHARGER_SENSORS + SCS.AC_CHARGER_SENSORS:
            # Use the ac_charger_name directly from the dictionary key
            sensor_name = f"{ac_charger_name} {description.name}"
            # entity_id = f"sensor.{sensor_name.lower().replace(' ', '_')}" # entity_id is usually handled by HA core based on unique_id

            entities.append(
                SigenergySensor(
                    coordinator=coordinator,
                    description=description,
                    name=sensor_name,
                    device_type=DEVICE_TYPE_AC_CHARGER,
                    device_id=str(slave_id),  # Use the retrieved slave_id
                    device_name=ac_charger_name,  # Use the name from the dictionary key
                )
            )

    # Log first 5 entities to see if they look valid
    if entities:  # Only call if list is not empty
        try:
            async_add_entities(entities)
        except Exception as ex:
            _LOGGER.exception(
                "[Setup Entry] Error during final async_add_entities call: %s", ex
            )
    else:
        _LOGGER.debug("[Setup Entry] No entities collected to add.")


class SigenergySensor(SigenergyEntity, SensorEntity):
    """Representation of a Sigenergy sensor."""

    entity_description: SigenergySensorEntityDescription

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SensorEntityDescription | SigenergySensorEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: Optional[str] = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the sensor."""
        try:
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

            # Sensor-specific initialization
            if (
                isinstance(description, SigenergySensorEntityDescription)
                and description.round_digits is not None
            ):
                self._round_digits = description.round_digits
            else:
                self._round_digits = None

        except Exception as ex:
            _LOGGER.exception(
                "[Sensor Init] Error initializing SigenergySensor '%s': %s", name, ex
            )  # Use exception

    def _decode_alarm_bits(self, value: int, alarm_mapping: dict) -> str:
        """Decode alarm bits into human-readable text.
        
        Args:
            value: The integer value of the alarm register
            alarm_mapping: Dictionary mapping bit positions to alarm descriptions
            
        Returns:
            A comma-separated string of active alarms, or "No Alarms" if no bits are set
        """
        if value is None or value == 0:
            return "No Alarms"
            
        active_alarms = []
        
        # Check each bit that's set in the value
        for bit_position in range(16):  # Most registers use 16 bits
            if value & (1 << bit_position):
                # If this bit is set, look up its meaning in the mapping
                if bit_position in alarm_mapping:
                    active_alarms.append(alarm_mapping[bit_position])
        
        if not active_alarms:
            return "Unknown Alarm"
            
        return ", ".join(active_alarms)

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        # --- Get the raw value first ---
        raw_value = None
        if self.coordinator.data is not None:
            try:
                if self._device_type == DEVICE_TYPE_PLANT:
                    raw_value = self.coordinator.data.get("plant", {}).get(self.entity_description.key)
                elif self._device_type == DEVICE_TYPE_INVERTER:
                    # PVStringSensor has its own native_value override, so this primarily handles direct inverter sensors.
                    raw_value = self.coordinator.data.get("inverters", {}).get(self._device_name, {}).get(self.entity_description.key)
                elif self._device_type == DEVICE_TYPE_AC_CHARGER:
                    raw_value = self.coordinator.data.get("ac_chargers", {}).get(self._device_name, {}).get(self.entity_description.key)
                elif self._device_type == DEVICE_TYPE_DC_CHARGER:
                     # Assuming DC charger data is nested under the inverter device name it's associated with
                     raw_value = self.coordinator.data.get("inverters", {}).get(self._device_name, {}).get(self.entity_description.key)
                # Add other device types if necessary

            except KeyError as e:
                _LOGGER.warning("KeyError retrieving raw_value for %s: %s", self.entity_id, e)
                raw_value = None
        else:
             _LOGGER.warning(
                 "[Sensor][native_value] No coordinator data available for %s when retrieving raw value.",
                 self.entity_id,
             )
             raw_value = None


        # --- Apply value_fn if it exists ---
        if (
            hasattr(self.entity_description, "value_fn")
            and self.entity_description.value_fn is not None
        ):
            # No need to check coordinator.data again, already checked above

            try:
                # Pass coordinator data if needed by the value_fn
                if (
                    hasattr(self.entity_description, "extra_fn_data")
                    and self.entity_description.extra_fn_data
                ):
                    # Pass extra parameters if available
                    extra_params = {
                        "device_name": self._device_name,
                        "pv_idx": self._pv_string_idx,
                        **(getattr(self.entity_description, "extra_params", {}) or {}),
                    }
                    # *** Pass the retrieved raw_value instead of None ***
                    transformed_value = self.entity_description.value_fn(
                        raw_value, # Pass the actual raw value
                        self.coordinator.data,
                        extra_params
                    )
                else:
                    # *** Pass the retrieved raw_value instead of None ***
                    # Also pass coordinator data and None for extra_params for consistency
                    transformed_value = self.entity_description.value_fn(
                        raw_value, # Pass the actual raw value
                        self.coordinator.data, # Still pass coordinator data for context if needed
                        None
                    )

                # Apply rounding if specified and value is numeric
                if transformed_value is not None and self._round_digits is not None:
                    try:
                        # Ensure the value is treated as Decimal for rounding
                        transformed_value = round(Decimal(transformed_value), self._round_digits)
                    except (TypeError, ValueError, InvalidOperation):
                        _LOGGER.warning("Could not round transformed value for %s: %s", self.entity_id, transformed_value)

                return transformed_value
            except Exception as ex:
                _LOGGER.error(
                    "Error applying value_fn for %s (raw_value=%r): %s",
                    self.entity_id,
                    raw_value,
                    ex,
                    exc_info=True, # Add traceback
                )
                # Return None on calculation error for numeric sensors
                if (
                    self.entity_description.native_unit_of_measurement is not None
                    or self.entity_description.state_class in [SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING, SensorStateClass.TOTAL]
                ):
                    return None
                return STATE_UNKNOWN


        # --- If no value_fn, proceed with direct use of raw_value ---

        # Handle missing or unknown values (using the raw_value retrieved earlier)
        if raw_value is None or str(raw_value).lower() == "unknown":
            # Return None for numeric sensors, STATE_UNKNOWN otherwise
            if (
                self.entity_description.native_unit_of_measurement is not None
                or self.entity_description.state_class
                in [SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING, SensorStateClass.TOTAL]
            ):
                return None
            return STATE_UNKNOWN

        # Use the already retrieved raw_value
        value = raw_value

        # Special handling for timestamp sensors
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            try:
                if not isinstance(value, (int, float)):
                    _LOGGER.warning(
                        "Invalid timestamp value type for %s: %s",
                        self.entity_id,
                        type(value),
                    )
                    return None
                if value == 0: # Treat 0 epoch as None/Unknown for timestamps
                    return None
                # Use epoch_to_datetime for timestamp conversion
                converted_timestamp = SC.epoch_to_datetime(value, self.coordinator.data)
                return converted_timestamp
            except Exception as ex:
                _LOGGER.error(
                    "Error converting timestamp for %s: %s", self.entity_id, ex
                )
                return None

        # Special handling for specific keys (alarms, enums, etc.)
        try:
            # Handle alarm codes
            if "alarm" in self.entity_description.key.lower():
                # Check which alarm register this is and use appropriate mapping
                if self.entity_description.key in ["plant_general_alarm1", "inverter_alarm1", "inverter_pcs_alarm1"]:
                    return self._decode_alarm_bits(value, ALARM_CODES["PCS_ALARM_CODES"])
                elif self.entity_description.key in ["plant_general_alarm2", "inverter_alarm2", "inverter_pcs_alarm2"]:
                    return self._decode_alarm_bits(value, ALARM_CODES["PCS_ALARM_CODES2"])
                elif self.entity_description.key in ["plant_general_alarm3", "inverter_alarm3", "inverter_ess_alarm"]:
                    return self._decode_alarm_bits(value, ALARM_CODES["ESS_ALARM_CODES"])
                elif self.entity_description.key in ["plant_general_alarm4", "inverter_alarm4", "inverter_gateway_alarm"]:
                    return self._decode_alarm_bits(value, ALARM_CODES["GATEWAY_ALARM_CODES"])
                elif self.entity_description.key in ["plant_general_alarm5", "inverter_alarm5", "inverter_dc_charger_alarm"]:
                    return self._decode_alarm_bits(value, ALARM_CODES["DC_CHARGER_ALARM_CODES"])
                elif self.entity_description.key == "ac_charger_alarm1":
                    return self._decode_alarm_bits(value, ALARM_CODES["AC_CHARGER_ALARM_CODES1"])
                elif self.entity_description.key == "ac_charger_alarm2":
                    return self._decode_alarm_bits(value, ALARM_CODES["AC_CHARGER_ALARM_CODES2"])
                elif self.entity_description.key == "ac_charger_alarm3":
                    return self._decode_alarm_bits(value, ALARM_CODES["AC_CHARGER_ALARM_CODES3"])
                # If alarm key doesn't match specific patterns, return raw value
                return value # Return raw value if no specific alarm mapping found

            # Other special cases (enum mappings)
            # Use a single try-except block for enums
            if self.entity_description.key == "plant_on_off_grid_status":
                return {
                    0: "On Grid",
                    1: "Off Grid (Auto)",
                    2: "Off Grid (Manual)",
                }.get(value, f"Unknown: {value}")
            if self.entity_description.key == "plant_running_state":
                return {
                    RunningState.STANDBY: "Standby",
                    RunningState.RUNNING: "Running",
                    RunningState.FAULT: "Fault",
                    RunningState.SHUTDOWN: "Shutdown",
                    RunningState.ENVIRONMENTAL_ABNORMALITY: "Environmental Abnormality",
                }.get(value, f"Unknown: {value}")
            if self.entity_description.key == "inverter_running_state":
                return {
                    RunningState.STANDBY: "Standby",
                    RunningState.RUNNING: "Running",
                    RunningState.FAULT: "Fault",
                    RunningState.SHUTDOWN: "Shutdown",
                    RunningState.ENVIRONMENTAL_ABNORMALITY: "Environmental Abnormality",
                }.get(value, f"Unknown: {value}")
            if self.entity_description.key == "ac_charger_system_state":
                return {
                    0: "Initializing",
                    1: "Not Connected",
                    2: "Reserving - Charger & EV Not Ready",
                    3: "Preparing - Charger Ready, EV Not Ready",
                    4: "EV Ready, Charger Not Ready",
                    5: "Charging",
                    6: "Fault",
                    7: "Error",
                }.get(value, f"Unknown: {value}")
            if self.entity_description.key == "inverter_output_type":
                 return {0: "Three Phase", 1: "Single Phase"}.get(value, f"Unknown: {value}")
            if self.entity_description.key == "plant_grid_sensor_status":
                 return {0: "Offline", 1: "Online"}.get(value, f"Unknown: {value}")

            # Apply rounding if specified and value is numeric
            if self._round_digits is not None:
                try:
                    # Ensure the value is treated as Decimal for rounding
                    value = round(Decimal(value), self._round_digits)
                except (TypeError, ValueError, InvalidOperation):
                     _LOGGER.warning("Could not round direct value for %s: %s", self.entity_id, value)
                     # Keep original value if rounding fails

        except Exception as ex:
            _LOGGER.error(
                "Error converting direct value for entity %s (%s): Value '%s' (type: %s). Error: %s",
                self.entity_id,
                self.entity_description.key,
                value,
                type(value).__name__,
                ex,
                exc_info=True, # Add traceback
            )
            # Return None on conversion error for numeric sensors
            if (
                self.entity_description.native_unit_of_measurement is not None
                or self.entity_description.state_class in [SensorStateClass.MEASUREMENT, SensorStateClass.TOTAL_INCREASING, SensorStateClass.TOTAL]
            ):
                return None
            return STATE_UNKNOWN

        # Return the final processed value
        return value

    # The 'available' property is now inherited from SigenergyEntity
    # If specific availability logic is needed for SigenergySensor beyond the base class, uncomment and modify this.


class PVStringSensor(SigenergySensor):
    """Representation of a PV String sensor."""

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergySensorEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: Optional[str] = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:

        super().__init__( # Calls SigenergySensor.__init__, which calls SigenergyEntity.__init__
            coordinator=coordinator,
            description=description,
            name=name,
            device_type=device_type,
            device_id=device_id,
            device_name=device_name,
            device_info=device_info,
            pv_string_idx=pv_string_idx,
        )

    # Add this property to PVStringSensor to check its availability based on parent inverter
    @property
    def available(self) -> bool:
        """Return if PV String entity is available based on parent inverter."""
        # Check coordinator status first
        if not self.coordinator.last_update_success:
            _LOGGER.warning(
                "PVStringSensor %s unavailable: Coordinator last update failed.",
                self.entity_id,
            )
            return False
        if not self.coordinator.data or "inverters" not in self.coordinator.data:
            _LOGGER.warning(
                "PVStringSensor %s unavailable: No coordinator data or 'inverters' key.",
                self.entity_id,
            )
            return False

        # Check if the parent inverter data exists using _device_name
        parent_inverter_available = (
            self._device_name in self.coordinator.data["inverters"]
        )

        return parent_inverter_available  # Base availability on parent inverter for now

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            _LOGGER.warning("No coordinator data available for %s", self.entity_id)
            return STATE_UNKNOWN

        try:
            # Access inverter data using device_name (inverter_name)
            inverter_data = self.coordinator.data["inverters"].get(
                self._device_name, {}
            )
            if not inverter_data:
                _LOGGER.warning(
                    "PVStringSensor %s native_value: No inverter data found for '%s'",
                    self.entity_id,
                    self._device_name,
                )
                return STATE_UNKNOWN  # Or None if preferred for numeric sensors

            value = None
            # First check if we have a value_fn (for calculated sensors)
            if (
                hasattr(self.entity_description, "value_fn")
                and self.entity_description.value_fn is not None
            ):
                try:
                    value = self.entity_description.value_fn(
                        None,  # value is not used directly by these functions
                        self.coordinator.data,
                        getattr(self.entity_description, "extra_params", {}),
                    )
                except Exception as fn_ex:
                    _LOGGER.error(
                        "PVStringSensor %s native_value: Error executing value_fn %s: %s",
                        self.entity_id,
                        self.entity_description.value_fn.__name__,
                        fn_ex,
                    )
                    value = None  # Or STATE_UNKNOWN

            else:
                data_key = (
                    f"inverter_pv{self._pv_string_idx}_{self.entity_description.key}"
                )
                value = inverter_data.get(data_key)

            # Final check and return
            if value is None:
                # _LOGGER.debug(
                #     "PVStringSensor %s native_value: Final value is None, returning None",
                #     self.entity_id,
                # )
                return None  # Return None for numeric sensors if value is missing

            # Attempt to convert to float if it's numeric, otherwise return as is
            try:
                # Check if it's already a number (int, float, Decimal)
                if isinstance(value, (int, float, Decimal)):
                    final_value = Decimal(value)  # Convert to Decimal for HA consistency
                else:
                    final_value = value  # Return non-numeric values directly
                # _LOGGER.debug("[PVStringSensor][%s] Reporting state: %s", self.entity_id, final_value)
                return final_value
            except (ValueError, TypeError) as conv_ex:
                _LOGGER.warning(
                    "PVStringSensor %s native_value: Could not convert final value '%s' to float: %s",
                    self.entity_id,
                    value,
                    conv_ex,
                )
                return STATE_UNKNOWN  # Or None

        except KeyError as ke:
            _LOGGER.error(
                "PVStringSensor %s native_value: KeyError accessing data: %s",
                self.entity_id,
                ke,
            )
            return STATE_UNKNOWN  # Or None
        except Exception as ex:
            _LOGGER.exception(
                "PVStringSensor %s native_value: Unexpected error getting value: %s",
                self.entity_id,
                ex,
            )
            return STATE_UNKNOWN  # Or None


class CoordinatorDiagnosticSensor(SigenergyEntity, SensorEntity):
    """Representation of a Sigenergy coordinator diagnostic sensor."""

    # Explicitly type entity_description for this class
    entity_description: SigenergySensorEntityDescription

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        coordinator = cast(SigenergyDataUpdateCoordinator, self.coordinator)
        key = self.entity_description.key
        value: float | None = None

        try:
            if key == "modbus_max_data_fetch_time":
                value = coordinator.largest_update_interval
            elif key == "modbus_latest_fetch_time_high":
                value = coordinator._latest_fetch_times[UpdateFrequencyType.HIGH]
            elif key == "modbus_latest_fetch_time_medium":
                value = coordinator._latest_fetch_times[UpdateFrequencyType.MEDIUM]
            elif key == "modbus_latest_fetch_time_low":
                value = coordinator._latest_fetch_times[UpdateFrequencyType.LOW]
            elif key == "modbus_latest_fetch_time_alarm":
                value = coordinator._latest_fetch_times[UpdateFrequencyType.ALARM]
            else:
                _LOGGER.warning("Unknown key for CoordinatorDiagnosticSensor: %s", key)
                return None

            # Ensure the final value is a float or None
            if value is None:
                return None
            return float(value)

        except (KeyError, ZeroDivisionError, TypeError, ValueError) as e:
            _LOGGER.warning(
                "Could not calculate value for %s (%s): %s", self.entity_id, key, e
            )
            return None
        except Exception as e:
            _LOGGER.exception(
                "Unexpected error calculating value for %s (%s): %s", self.entity_id, key, e
            )
            return None

