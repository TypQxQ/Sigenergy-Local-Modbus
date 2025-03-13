"""Calculated sensor implementations for Sigenergy integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Optional
from decimal import Decimal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
# from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
    EntityCategory,
    UnitOfPower,
)

from .const import (
    EMSWorkMode,
    DEFAULT_MAX_SUB_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class SigenergyCalculations:
    """Static class for Sigenergy calculated sensor functions."""
    
    # Class variable to store last power readings and timestamps for energy calculation
    _power_history = {}  # Format: {(device_id, pv_idx): {'last_timestamp': datetime, 'last_power': float, 'accumulated_energy': float}}
    
    @dataclass
    class SigenergySensorEntityDescription(SensorEntityDescription):
        """Class describing Sigenergy sensor entities."""

        entity_registry_enabled_default: bool = True
        value_fn: Optional[Callable[[Any, Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Any]] = None
        extra_fn_data: Optional[bool] = False  # Flag to indicate if value_fn needs coordinator data
        extra_params: Optional[Dict[str, Any]] = None  # Additional parameters for value_fn

        @classmethod
        def from_entity_description(cls, description, 
                                    value_fn: Optional[Callable[[Any, Optional[Dict[str, Any]], Optional[Dict[str, Any]]], Any]] = None,
                                    extra_fn_data: Optional[bool] = False,
                                    extra_params: Optional[Dict[str, Any]] = None):
            """Create a SigenergySensorEntityDescription instance from a SensorEntityDescription."""
            # Create a new instance with the base attributes
            if isinstance(description, cls):
                # If it's already our class, copy all attributes
                result = cls(
                    key=description.key,
                    name=description.name,
                    device_class=description.device_class,
                    native_unit_of_measurement=description.native_unit_of_measurement,
                    state_class=description.state_class,
                    entity_registry_enabled_default=description.entity_registry_enabled_default,
                    value_fn=value_fn or description.value_fn,
                    extra_fn_data=extra_fn_data if extra_fn_data is not None else description.extra_fn_data,
                    extra_params=extra_params or description.extra_params
                )
            else:
                # It's a regular SensorEntityDescription
                result = cls(
                    key=description.key,
                    name=description.name,
                    device_class=getattr(description, "device_class", None),
                    native_unit_of_measurement=getattr(description, "native_unit_of_measurement", None),
                    state_class=getattr(description, "state_class", None),
                    entity_registry_enabled_default=getattr(description, "entity_registry_enabled_default", True),
                    value_fn=value_fn,
                    extra_fn_data=extra_fn_data,
                    extra_params=extra_params
                )
            
            # Copy any other attributes that might exist
            for attr_name in dir(description):
                if not attr_name.startswith('_') and attr_name not in ['key', 'name', 'device_class', 
                                                                    'native_unit_of_measurement', 'state_class', 
                                                                    'entity_registry_enabled_default', 'value_fn',
                                                                    'extra_fn_data', 'extra_params']:
                    setattr(result, attr_name, getattr(description, attr_name))
            
            return result

    @staticmethod
    def minutes_to_gmt(minutes: Any) -> str:
        """Convert minutes offset to GMT format."""
        if minutes is None:
            return None
        try:
            hours = int(minutes) // 60
            return f"GMT{'+' if hours >= 0 else ''}{hours}"
        except (ValueError, TypeError):
            return None

    @staticmethod
    def epoch_to_datetime(epoch: Any, coordinator_data: Optional[dict] = None) -> Optional[datetime]:
        """Convert epoch timestamp to datetime using system's configured timezone."""
        _LOGGER.debug("Converting epoch timestamp: %s (type: %s)", epoch, type(epoch))
        if epoch is None or epoch == 0:  # Also treat 0 as None for timestamps
            return None

        try:
            # Convert epoch to integer if it isn't already
            epoch_int = int(epoch)
            
            # Create timezone based on coordinator data if available
            if coordinator_data and "plant" in coordinator_data:
                try:
                    tz_offset = coordinator_data["plant"].get("plant_system_timezone")
                    if tz_offset is not None:
                        tz_minutes = int(tz_offset)
                        tz_hours = tz_minutes // 60
                        tz_remaining_minutes = tz_minutes % 60
                        tz = timezone(timedelta(hours=tz_hours, minutes=tz_remaining_minutes))
                    else:
                        tz = timezone.utc
                except (ValueError, TypeError):
                    _LOGGER.warning("Invalid timezone offset in coordinator data, using UTC")
                    tz = timezone.utc
            else:
                tz = timezone.utc
                
            # Additional validation for timestamp range
            if epoch_int < 0 or epoch_int > 32503680000:  # Jan 1, 3000
                _LOGGER.warning("Timestamp %s out of reasonable range", epoch_int)
                return None

            try:
                # Convert timestamp using the determined timezone
                dt = datetime.fromtimestamp(epoch_int, tz=tz)
                _LOGGER.debug("Converted epoch %s to datetime %s with timezone %s", epoch_int, dt, tz)
                return dt
            except (OSError, OverflowError) as ex:
                _LOGGER.warning("Invalid timestamp value %s: %s", epoch_int, ex)
                return None
            
        except (ValueError, TypeError, OSError) as ex:
            _LOGGER.warning("Error converting epoch %s to datetime: %s", epoch, ex)
            return None

    @staticmethod
    def calculate_pv_power(_, coordinator_data: Optional[Dict[str, Any]] = None, extra_params: Optional[Dict[str, Any]] = None) -> Optional[float]:
        """Calculate PV string power with proper error handling."""
        if not coordinator_data or not extra_params:
            _LOGGER.debug("Missing required data for PV power calculation")
            return None
            
        try:
            pv_idx = extra_params.get("pv_idx")
            device_id = extra_params.get("device_id")
            
            if not pv_idx or not device_id:
                _LOGGER.debug("Missing PV string index or device ID for power calculation")
                return None
                
            inverter_data = coordinator_data.get("inverters", {}).get(device_id, {})
            if not inverter_data:
                _LOGGER.debug("No inverter data available for power calculation")
                return None
                
            v_key = f"inverter_pv{pv_idx}_voltage"
            c_key = f"inverter_pv{pv_idx}_current"
            
            pv_voltage = inverter_data.get(v_key)
            pv_current = inverter_data.get(c_key)
            
            # Validate inputs
            if pv_voltage is None or pv_current is None:
                _LOGGER.debug("Missing voltage or current data for PV string %d", pv_idx)
                return None
                
            if not isinstance(pv_voltage, (int, float)) or not isinstance(pv_current, (int, float)):
                _LOGGER.debug("Invalid data types for PV string %d: voltage=%s, current=%s",
                            pv_idx, type(pv_voltage), type(pv_current))
                return None
                
            # Calculate power with bounds checking
            # Make sure we don't return unreasonable values
            power = pv_voltage * pv_current  # Already in Watts since voltage is in V and current in A
            
            # Apply some reasonable bounds
            MAX_REASONABLE_POWER = 20000  # 20kW per string is very high already
            if abs(power) > MAX_REASONABLE_POWER:
                _LOGGER.warning("Calculated power for PV string %d seems excessive: %s W",
                            pv_idx, power)
                
            return power / 1000  # Convert to kW
        except Exception as ex:
            _LOGGER.warning("Error calculating power for PV string %d: %s",
                        extra_params.get("pv_idx", "unknown"), ex)
            return None

    @staticmethod
    def calculate_accumulated_energy(value: Any, coordinator_data: Optional[Dict[str, Any]] = None, extra_params: Optional[Dict[str, Any]] = None) -> Optional[float]:
        """Calculate accumulated energy for a PV string based on integration of power over time.
        
        This function integrates power over time to calculate accumulated energy (kWh).
        It stores the last power reading and timestamp for each PV string to calculate
        energy between consecutive readings using the trapezoidal integration method.
        """
        if not coordinator_data or not extra_params:
            _LOGGER.debug("Missing required data for energy calculation")
            return None
            
        try:
            pv_idx = extra_params.get("pv_idx")
            device_id = extra_params.get("device_id")
            
            if not pv_idx or not device_id:
                _LOGGER.debug("Missing PV string index or device ID for energy calculation")
                return None
                
            # Calculate current power using the same method as calculate_pv_power
            inverter_data = coordinator_data.get("inverters", {}).get(device_id, {})
            if not inverter_data:
                _LOGGER.debug("No inverter data available for energy calculation")
                return None
                
            v_key = f"inverter_pv{pv_idx}_voltage"
            c_key = f"inverter_pv{pv_idx}_current"
            
            pv_voltage = inverter_data.get(v_key)
            pv_current = inverter_data.get(c_key)
            
            # Validate inputs
            if pv_voltage is None or pv_current is None:
                _LOGGER.debug("Missing voltage or current data for PV string %s", pv_idx)
                return None
                
            if not isinstance(pv_voltage, (int, float)) or not isinstance(pv_current, (int, float)):
                _LOGGER.debug("Invalid data types for PV string %s: voltage=%s, current=%s",
                            pv_idx, type(pv_voltage), type(pv_current))
                return None
                
            # Calculate current power in kW with more precise decimal handling
            try:
                current_power = Decimal(str(pv_voltage * pv_current)) / Decimal('1000')  # Convert W to kW
            except (ValueError, TypeError):
                _LOGGER.debug("Could not convert power values to Decimal")
                current_power = Decimal(str((pv_voltage * pv_current) / 1000))  # Convert W to kW
            
            # Apply some reasonable bounds
            MAX_REASONABLE_POWER = Decimal('20')  # 20kW per string is very high already
            if abs(current_power) > MAX_REASONABLE_POWER:
                _LOGGER.warning("Calculated power for PV string %s seems excessive: %s kW",
                            pv_idx, current_power)
            
            # Get current time with proper timezone awareness
            current_time = datetime.now(timezone.utc)
            
            # Create a unique key for this PV string
            key = (device_id, pv_idx)
            
            # Initialize entry in history if it doesn't exist
            if key not in SigenergyCalculations._power_history:
                SigenergyCalculations._power_history[key] = {
                    'last_timestamp': current_time,
                    'last_power': current_power,
                    'accumulated_energy': Decimal('0.0')
                }
                _LOGGER.debug("Initialized energy tracking for PV string %s on device %s", 
                             pv_idx, device_id)
                return 0.0
            
            # Retrieve history for this PV string
            history = SigenergyCalculations._power_history[key]
            
            # Calculate time difference in seconds with Decimal precision for accurate integration
            try:
                time_diff_seconds = Decimal(str((current_time - history['last_timestamp']).total_seconds()))
            except (ValueError, TypeError):
                _LOGGER.debug("Could not convert time difference to Decimal")
                time_diff_seconds = Decimal(str((current_time - history['last_timestamp']).total_seconds()))
            
            # If the time difference is too large, it might indicate a system restart or another issue
            # Use the DEFAULT_MAX_SUB_INTERVAL constant from const.py (default: 30 seconds)
            if time_diff_seconds <= Decimal('0') or time_diff_seconds > Decimal(str(DEFAULT_MAX_SUB_INTERVAL)):
                _LOGGER.debug("Time difference outside allowed range for energy calculation: %s seconds (max allowed: %s)",
                            time_diff_seconds, DEFAULT_MAX_SUB_INTERVAL)
                history['last_timestamp'] = current_time
                history['last_power'] = current_power
                return float(history['accumulated_energy'])
            
            # Convert time difference to hours for energy calculation (kW * h = kWh)
            time_diff_hours = time_diff_seconds / Decimal('3600')
            
            # Calculate energy for this time period using trapezoidal integration method
            # E = P_avg * t where P_avg = (P1 + P2) / 2 and t is time in hours
            average_power = (history['last_power'] + current_power) / Decimal('2')
            
            # Only accumulate positive energy values (when PV is generating power)
            energy_this_period = max(Decimal('0'), average_power * time_diff_hours)
            
            # Add to accumulated energy
            history['accumulated_energy'] += energy_this_period
            
            # Update history with current values for next calculation
            history['last_timestamp'] = current_time
            history['last_power'] = current_power
            
            _LOGGER.debug("PV string %s energy calculation: +%s kWh this period, total: %s kWh", 
                         pv_idx, energy_this_period, history['accumulated_energy'])
            
            # Return float value for compatibility with Home Assistant
            return float(history['accumulated_energy'])
            
        except Exception as ex:
            _LOGGER.warning("Error calculating accumulated energy for PV string %s: %s",
                        extra_params.get("pv_idx", "unknown"), ex)
            return None

class SigenergyCalculatedSensors:
    """Class for holding calculated sensor methods."""

    PV_STRING_SENSORS = [
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="power",
            name="Power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            suggested_display_precision=2,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=SigenergyCalculations.calculate_pv_power,
            extra_fn_data=True,
        ),
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="accumulated_energy",
            name="Accumulated Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_display_precision=3,
            value_fn=SigenergyCalculations.calculate_accumulated_energy,
            extra_fn_data=True,
        ),
    ]

    PLANT_SENSORS = [
        # System time and timezone
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="plant_system_time",
            name="System Time",
            icon="mdi:clock",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=SigenergyCalculations.epoch_to_datetime,
            extra_fn_data=True,  # Indicates that this sensor needs coordinator data for timestamp conversion
        ),
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="plant_system_timezone",
            name="System Timezone",
            icon="mdi:earth",
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=SigenergyCalculations.minutes_to_gmt,
        ),
        # EMS Work Mode sensor with value mapping
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="plant_ems_work_mode",
            name="EMS Work Mode",
            icon="mdi:home-battery",
            value_fn=lambda value: {
                EMSWorkMode.MAX_SELF_CONSUMPTION: "Maximum Self Consumption",
                EMSWorkMode.AI_MODE: "AI Mode",
                EMSWorkMode.TOU: "Time of Use",
                EMSWorkMode.REMOTE_EMS: "Remote EMS",
            }.get(value, "Unknown"),
        ),
    ]

    INVERTER_SENSORS = [
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="inverter_startup_time",
            name="Startup Time",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=SigenergyCalculations.epoch_to_datetime,
            extra_fn_data=True,  # Indicates that this sensor needs coordinator data for timestamp conversion
        ),
        SigenergyCalculations.SigenergySensorEntityDescription(
            key="inverter_shutdown_time",
            name="Shutdown Time",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=SigenergyCalculations.epoch_to_datetime,
            extra_fn_data=True,  # Indicates that this sensor needs coordinator data for timestamp conversion
        ),
    ]

    AC_CHARGER_SENSORS = []

    DC_CHARGER_SENSORS = []