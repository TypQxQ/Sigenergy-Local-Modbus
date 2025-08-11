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
import inspect
from .const import (
    DOMAIN,
    DEVICE_TYPE_PLANT,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    CONF_SLAVE_ID,
    CONF_INVERTER_HAS_DCCHARGER,
)
from .sigen_entity import SigenergyEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sigenergy sensor platform."""
    coordinator: SigenergyDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]["coordinator"]
    plant_name = config_entry.data[CONF_NAME]
    entities_to_add = []

    # Helper to add entities to the list
    def add_entities_for_device(device_name, device_conn, entity_descriptions, entity_class, device_type, **kwargs):
        entities_to_add.extend(
            generate_sigen_entity(
                plant_name,
                device_name,
                device_conn,
                coordinator,
                entity_class,
                entity_descriptions,
                device_type,
                **kwargs,
            )
        )

    # Plant Sensors
    add_entities_for_device(None, None, SS.PLANT_SENSORS, SigenergySensor, DEVICE_TYPE_PLANT)
    add_entities_for_device(None, None, SCS.PLANT_SENSORS, SigenergySensor, DEVICE_TYPE_PLANT)
    add_entities_for_device(None, None, SCS.PLANT_INTEGRATION_SENSORS, SigenergyIntegrationSensor, DEVICE_TYPE_PLANT, hass=hass)
    add_entities_for_device(None, None, list(COORDINATOR_DIAGNOSTIC_SENSORS), CoordinatorDiagnosticSensor, DEVICE_TYPE_PLANT)

    # Inverter and related sensors
    for device_name, device_conn in coordinator.hub.inverter_connections.items():
        add_entities_for_device(device_name, device_conn, SS.INVERTER_SENSORS, SigenergySensor, DEVICE_TYPE_INVERTER)
        add_entities_for_device(device_name, device_conn, SCS.INVERTER_SENSORS, SigenergySensor, DEVICE_TYPE_INVERTER)
        add_entities_for_device(device_name, device_conn, SCS.INVERTER_INTEGRATION_SENSORS, SigenergyIntegrationSensor, DEVICE_TYPE_INVERTER, hass=hass)

        # PV Strings
        inverter_data = (coordinator.data or {}).get("inverters", {}).get(device_name, {})
        pv_string_count = inverter_data.get("inverter_pv_string_count", 0)
        if isinstance(pv_string_count, (int, float)) and pv_string_count > 0:
            for pv_idx in range(1, int(pv_string_count) + 1):
                try:
                    pv_string_name = f"{device_name} PV{pv_idx}"
                    parent_inverter_id = f"{coordinator.hub.config_entry.entry_id}_{generate_device_id(device_name)}"
                    pv_string_id = f"{parent_inverter_id}_pv{pv_idx}"
                    pv_device_info = DeviceInfo(
                        identifiers={(DOMAIN, pv_string_id)},
                        name=pv_string_name,
                        manufacturer="Sigenergy",
                        model="PV String",
                        via_device=(DOMAIN, parent_inverter_id),
                    )
                    add_entities_for_device(device_name, device_conn, SS.PV_STRING_SENSORS, PVStringSensor, DEVICE_TYPE_INVERTER, hass=hass, device_info=pv_device_info, pv_string_idx=pv_idx)
                    add_entities_for_device(device_name, device_conn, SCS.PV_STRING_SENSORS, PVStringSensor, DEVICE_TYPE_INVERTER, hass=hass, device_info=pv_device_info, pv_string_idx=pv_idx)
                    add_entities_for_device(device_name, device_conn, SCS.PV_INTEGRATION_SENSORS, SigenergyIntegrationSensor, DEVICE_TYPE_INVERTER, hass=hass, device_info=pv_device_info, pv_string_idx=pv_idx)
                except Exception as ex:
                    _LOGGER.exception("Error creating sensors for PV string %d of inverter %s: %s", pv_idx, device_name, ex)

        # DC Charger
        if device_conn.get(CONF_INVERTER_HAS_DCCHARGER, False):
            dc_name = f"{device_name} DC Charger"
            parent_inverter_id = f"{coordinator.hub.config_entry.entry_id}_{generate_device_id(device_name)}"
            dc_id = f"{parent_inverter_id}_dc_charger"
            dc_device_info = DeviceInfo(
                identifiers={(DOMAIN, dc_id)},
                name=dc_name,
                manufacturer="Sigenergy",
                model="DC Charger",
                via_device=(DOMAIN, parent_inverter_id),
            )
            add_entities_for_device(device_name, device_conn, SS.DC_CHARGER_SENSORS, SigenergySensor, DEVICE_TYPE_DC_CHARGER, device_info=dc_device_info)

    # AC Charger Sensors
    for ac_charger_name, ac_details in coordinator.hub.ac_charger_connections.items():
        slave_id = ac_details.get(CONF_SLAVE_ID)
        if slave_id is None:
            _LOGGER.warning("Missing slave ID for AC charger '%s', skipping.", ac_charger_name)
            continue
        
        # Combine sensor descriptions for AC chargers
        ac_charger_sensors = SS.AC_CHARGER_SENSORS + SCS.AC_CHARGER_SENSORS
        for description in ac_charger_sensors:
            sensor_name = f"{ac_charger_name} {description.name}"
            entities_to_add.append(
                SigenergySensor(
                    coordinator=coordinator,
                    description=description,
                    name=sensor_name,
                    device_type=DEVICE_TYPE_AC_CHARGER,
                    device_id=str(slave_id),
                    device_name=ac_charger_name,
                )
            )

    if entities_to_add:
        async_add_entities(entities_to_add)
        _LOGGER.debug("Added %d sensor entities", len(entities_to_add))
        entity_unique_ids = [entity._attr_unique_id for entity in entities_to_add if hasattr(entity, '_attr_unique_id')]
        _LOGGER.debug("Added sensor entity unique IDs: %s", ", ".join(entity_unique_ids))
    else:
        _LOGGER.debug("No sensor entities to add.")


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
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the sensor."""
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
        self._round_digits = (
            description.round_digits
            if isinstance(description, SigenergySensorEntityDescription)
            else None
        )

    def _decode_alarm_bits(self, value: int, alarm_mapping: dict) -> str:
        """Decode alarm bits into human-readable text."""
        if value is None or value == 0:
            return "No Problem"
            
        active_alarms = [
            desc for bit, desc in alarm_mapping.items() if value & (1 << bit)
        ]
        
        if not active_alarms:
            return "Unknown Alarm"
            
        return ", ".join(active_alarms)

    def _get_raw_value(self) -> Any:
        """Retrieve the raw value from coordinator data for this entity."""
        data = self.coordinator.data or {}
        if self._device_type == DEVICE_TYPE_PLANT:
            return data.get("plant", {}).get(self.entity_description.key)
        if self._device_type == DEVICE_TYPE_INVERTER:
            return data.get("inverters", {}).get(self._device_name, {}).get(self.entity_description.key)
        if self._device_type == DEVICE_TYPE_AC_CHARGER:
            return data.get("ac_chargers", {}).get(self._device_name, {}).get(self.entity_description.key)
        if self._device_type == DEVICE_TYPE_DC_CHARGER:
            return data.get("dc_chargers", {}).get(self._device_name, {}).get(self.entity_description.key)
        return None

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        raw_value = self._get_raw_value()
        data = self.coordinator.data
        if data is None:
            return None

        if hasattr(self.entity_description, "value_fn") and self.entity_description.value_fn:
            try:
                # Call transformation function, trying 3,2,1 args for compatibility
                fn = self.entity_description.value_fn
                extra_params = getattr(self.entity_description, "extra_params", {}) or {}
                transformed = None
                for args in [(raw_value, data, extra_params), (raw_value, data), (raw_value,)]:
                    try:
                        transformed = fn(*args)
                        break
                    except TypeError:
                        continue

                # Round if needed
                if transformed is not None and self._round_digits is not None:
                    return round(Decimal(transformed), self._round_digits)
                return transformed
            except Exception as ex:
                _LOGGER.error("Error in value_fn for %s: %s", self.entity_id, ex, exc_info=True)
                return None if self.entity_description.state_class else STATE_UNKNOWN

        # No transformation function, handle raw_value
        if raw_value is None:
            return None if self.entity_description.state_class else STATE_UNKNOWN

        # Handle special data types
        if self.entity_description.device_class == SensorDeviceClass.TIMESTAMP:
            return SC.epoch_to_datetime(raw_value, data) if raw_value else None

        # Handle alarm codes
        if "alarm" in self.entity_description.key.lower():
            # Handle PCS alarms (plant_general_alarm1/2 and inverter_alarm1/2)
            if self.entity_description.key in ["plant_general_alarm1", "inverter_alarm1"]:
                return self._decode_alarm_bits(raw_value, ALARM_CODES["PCS_ALARM_CODES"])
            elif self.entity_description.key in ["plant_general_alarm2", "inverter_alarm2"]:
                return self._decode_alarm_bits(raw_value, ALARM_CODES["PCS_ALARM_CODES2"])
            # Handle ESS alarms
            elif self.entity_description.key in ["plant_general_alarm3", "inverter_alarm3", "inverter_ess_alarm"]:
                return self._decode_alarm_bits(raw_value, ALARM_CODES["ESS_ALARM_CODES"])
            # Handle Gateway alarms
            elif self.entity_description.key in ["plant_general_alarm4", "inverter_alarm4", "inverter_gateway_alarm"]:
                return self._decode_alarm_bits(raw_value, ALARM_CODES["GATEWAY_ALARM_CODES"])
            # Handle DC Charger alarms
            elif self.entity_description.key in ["plant_general_alarm5", "inverter_alarm5", "inverter_dc_charger_alarm"]:
                return self._decode_alarm_bits(raw_value, ALARM_CODES["DC_CHARGER_ALARM_CODES"])
            # Handle AC Charger alarms
            elif self.entity_description.key == "ac_charger_alarm1":
                return self._decode_alarm_bits(raw_value, ALARM_CODES["AC_CHARGER_ALARM_CODES1"])
            elif self.entity_description.key == "ac_charger_alarm2":
                return self._decode_alarm_bits(raw_value, ALARM_CODES["AC_CHARGER_ALARM_CODES2"])
            elif self.entity_description.key == "ac_charger_alarm3":
                return self._decode_alarm_bits(raw_value, ALARM_CODES["AC_CHARGER_ALARM_CODES3"])

        # Handle enums
        enum_maps = {
            "plant_on_off_grid_status": {0: "On Grid", 1: "Off Grid (Auto)", 2: "Off Grid (Manual)"},
            "plant_running_state": {s.value: s.name.replace("_", " ").title() for s in RunningState},
            "inverter_running_state": {s.value: s.name.replace("_", " ").title() for s in RunningState},
            "ac_charger_system_state": {0: "Initializing", 1: "Not Connected", 2: "Reserving", 3: "Preparing", 4: "EV Ready", 5: "Charging", 6: "Fault", 7: "Error"},
            "inverter_output_type": {0: "Three Phase", 1: "Single Phase"},
            "plant_grid_sensor_status": {0: "Offline", 1: "Online"},
        }
        if self.entity_description.key in enum_maps:
            return enum_maps[self.entity_description.key].get(raw_value, f"Unknown: {raw_value}")

        if self._round_digits is not None:
            try:
                return round(Decimal(raw_value), self._round_digits)
            except (TypeError, ValueError, InvalidOperation):
                _LOGGER.warning("Could not round direct value for %s: %s", self.entity_id, raw_value)

        return raw_value


class PVStringSensor(SigenergySensor):
    """Representation of a PV String sensor."""

    def __init__(
        self,
        coordinator: SigenergyDataUpdateCoordinator,
        description: SigenergySensorEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
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

    @property
    def available(self) -> bool:
        """Return if PV String entity is available based on parent inverter."""
        return (
            super().available and
            self._device_name in (self.coordinator.data or {}).get("inverters", {})
        )

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if not self.available or self.coordinator.data is None:
            return None

        inverter_data = self.coordinator.data.get("inverters", {}).get(self._device_name, {})
        
        if hasattr(self.entity_description, "value_fn") and self.entity_description.value_fn:
            try:
                return self.entity_description.value_fn(
                    None,
                    self.coordinator.data,
                    getattr(self.entity_description, "extra_params", {}),
                )
            except Exception as ex:
                _LOGGER.error("Error in PVStringSensor value_fn for %s: %s", self.entity_id, ex)
                return None
        
        data_key = f"inverter_pv{self._pv_string_idx}_{self.entity_description.key}"
        value = inverter_data.get(data_key)

        if value is None:
            return None
            
        try:
            return Decimal(value)
        except (ValueError, TypeError):
            return value


class CoordinatorDiagnosticSensor(SigenergyEntity, SensorEntity):
    """Representation of a Sigenergy coordinator diagnostic sensor."""

    # Explicitly type entity_description for this class
    entity_description: SigenergySensorEntityDescription

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        coordinator = cast(SigenergyDataUpdateCoordinator, self.coordinator)
        key = self.entity_description.key
        
        try:
            if key == "modbus_max_data_fetch_time":
                value = coordinator.largest_update_interval
            else:
                value = coordinator.latest_fetch_time
            
            return float(value) if value is not None else None
        except (TypeError, ValueError) as e:
            _LOGGER.warning("Could not calculate value for %s: %s", self.entity_id, e)
            return None
        except Exception as e:
            _LOGGER.exception("Unexpected error in CoordinatorDiagnosticSensor for %s: %s", self.entity_id, e)
            return None

