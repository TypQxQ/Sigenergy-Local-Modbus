"""Calculated sensor implementations for Sigenergy integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, Optional, TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
    RestoreSensor,
)
from homeassistant.const import (
    UnitOfEnergy,
    EntityCategory,
    UnitOfPower,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback, State
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_call_later
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.util import dt as dt_util

from .const import CONF_VALUES_TO_INIT, DEFAULT_MIN_INTEGRATION_TIME
from .modbusregisterdefinitions import EMSWorkMode

from .common import (
    SigenergySensorEntityDescription,
    safe_decimal,
    safe_float,
)
from .sigen_entity import SigenergyEntity # Import the new base class

if TYPE_CHECKING:
    from .coordinator import SigenergyDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Constants for daily sensor reset
DAILY_RESET_HOUR = 0
DAILY_RESET_MINUTE = 0
DAILY_RESET_SECOND = 0

# Only log for these entities
LOG_THIS_ENTITY = [
    # "sensor.sigen_plant_daily_pv_energy",
]


class SigenergyCalculations:
    """Static class for Sigenergy calculated sensor functions."""

    # Class variable to store last power readings and timestamps for energy calculation
    _power_history = {}

    @staticmethod
    def minutes_to_gmt(minutes: Any) -> Optional[str]:
        """Convert minutes offset to GMT format."""
        if minutes is None:
            return None
        try:
            hours = int(minutes) // 60
            return f"GMT{'+' if hours >= 0 else ''}{hours}"
        except (ValueError, TypeError):
            return None

    @staticmethod
    def epoch_to_datetime(
        epoch: Any, coordinator_data: Optional[dict] = None
    ) -> Optional[datetime]:
        """Convert epoch timestamp to datetime using system's configured timezone."""
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
                        tz = timezone(
                            timedelta(hours=tz_hours, minutes=tz_remaining_minutes)
                        )
                    else:
                        tz = timezone.utc
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "[CS][Timestamp] Invalid timezone in coordinator data: %s", e
                    )
                    tz = timezone.utc
            else:
                tz = timezone.utc

            # Additional validation for timestamp range
            if epoch_int < 0 or epoch_int > 32503680000:  # Jan 1, 3000
                _LOGGER.warning(
                    "[CS][Timestamp] Value %s out of reasonable range [0, 32503680000]",
                    epoch_int,
                )
                return None

            try:
                # Convert timestamp using the determined timezone
                dt = datetime.fromtimestamp(epoch_int, tz=tz)
                return dt
            except (OSError, OverflowError) as ex:
                _LOGGER.warning(
                    "[CS][Timestamp] Invalid timestamp %s: %s", epoch_int, ex
                )
                return None

        except (ValueError, TypeError, OSError) as ex:
            _LOGGER.warning("[CS][Timestamp] Conversion error for %s: %s", epoch, ex)
            return None

    @staticmethod
    def calculate_total_pv_power(
        _,  # value is not used for this calculation
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None, # Not used here, but kept for consistency
    ) -> Optional[float]:
        """Calculate the total PV power from plant and party sources."""
        if not coordinator_data or "plant" not in coordinator_data:
            _LOGGER.debug("[CS][Total PV Power] Missing plant data in coordinator_data for total PV power calculation")
            return None

        plant_data = coordinator_data.get("plant", {})

        plant_pv_power = safe_float(
            plant_data.get("plant_sigen_photovoltaic_power"))
        thirdparty_pv_power = safe_float(
            plant_data.get("plant_third_party_photovoltaic_power"))

        # If either value is None after safe_float, it means it was invalid or missing.
        # We treat missing as 0 for summation, but if both are missing, return None.
        if plant_pv_power is None and thirdparty_pv_power is None:
            _LOGGER.debug("[CS][Total PV Power] Both plant_photovoltaic_power and thirdparty_pv_power are unavailable.")
            return None

        return safe_float((plant_pv_power or 0.0) + (thirdparty_pv_power or 0.0))

    @staticmethod
    def calculate_pv_power(
        _,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[float]:
        """Calculate PV string power with proper error handling."""
        if not coordinator_data or not extra_params:
            _LOGGER.warning("Missing required data for PV power calculation")
            return None

        try:
            pv_idx = extra_params.get("pv_idx")
            # Expect device_name instead of device_id
            device_name = extra_params.get("device_name")

            if not pv_idx or not device_name:
                _LOGGER.warning(
                    "Missing PV string index or device name for power calculation from extra_params: %s",
                    extra_params,
                )
                return None

            # Use device_name to look up inverter data
            inverter_data = coordinator_data.get("inverters", {}).get(device_name, {})

            if not inverter_data:
                _LOGGER.warning(
                    "[CS][PV Power] No inverter data available for power calculation"
                )
                return None

            v_key = f"inverter_pv{pv_idx}_voltage"
            c_key = f"inverter_pv{pv_idx}_current"

            pv_voltage = inverter_data.get(v_key)
            pv_current = inverter_data.get(c_key)

            # Validate inputs
            if pv_voltage is None or pv_current is None:
                _LOGGER.warning(
                    "[CS][PV Power] Missing voltage or current data for PV string %d",
                    pv_idx,
                )
                return None

            if not isinstance(pv_voltage, (int, float)) or not isinstance(
                pv_current, (int, float)
            ):
                _LOGGER.warning(
                    "Invalid data types for PV string %d: voltage=%s, current=%s",
                    pv_idx,
                    type(pv_voltage),
                    type(pv_current),
                )
                return None

            # Calculate power with bounds checking
            # Convert to Decimal for precise calculation
            try:
                voltage_dec = safe_decimal(pv_voltage)
                current_dec = safe_decimal(pv_current)
                if voltage_dec and current_dec:
                    power = voltage_dec * current_dec  # Already in Watts
                else:
                    return 0.0
            except (ValueError, TypeError, InvalidOperation):
                _LOGGER.warning(
                    "[CS][PV Power] Error converting values to Decimal: V=%s, I=%s",
                    pv_voltage,
                    pv_current,
                )
                return None

            # Apply some reasonable bounds
            MAX_REASONABLE_POWER = Decimal(
                "20000"
            )  # 20kW per string is very high already
            if isinstance(power, Decimal) and abs(power) > MAX_REASONABLE_POWER:
                _LOGGER.warning(
                    "[CS][PV Power] Calculated power for PV string %d seems excessive: %s W",
                    pv_idx,
                    power,
                )
            elif not isinstance(power, Decimal) and abs(power) > float(
                MAX_REASONABLE_POWER
            ):
                _LOGGER.warning(
                    "[CS][PV Power] Calculated power for PV string %d seems excessive: %s W",
                    pv_idx,
                    power,
                )

            # Convert to kW
            if isinstance(power, Decimal):
                final_power = power / Decimal("1000")
            else:
                final_power = power / 1000

            return (
                safe_float(final_power) if isinstance(final_power, Decimal) else final_power
            )
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOGGER.warning(
                "[CS]Error calculating power for PV string %d: %s",
                extra_params.get("pv_idx", "unknown"),
                ex,
            )
            return None

    @staticmethod
    def calculate_grid_import_power(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate grid import power (positive values only)."""
        if coordinator_data is None or "plant" not in coordinator_data:
            return None

        # Get the grid active power value from coordinator data
        grid_power = coordinator_data["plant"].get("plant_grid_sensor_active_power")

        if grid_power is None or not isinstance(grid_power, (int, float)):
            return None

        # Convert to Decimal for precise calculation
        try:
            power_dec = safe_decimal(grid_power)
            # Return value if positive, otherwise 0
            return power_dec if power_dec and power_dec > Decimal("0") else Decimal("0.0")
        except (ValueError, TypeError, InvalidOperation):
            # Fallback to float calculation
            return safe_decimal(grid_power) if grid_power > 0 else Decimal("0.0")

    @staticmethod
    def calculate_grid_export_power(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate grid export power (negative values converted to positive)."""
        if coordinator_data is None or "plant" not in coordinator_data:
            return None

        # Get the grid active power value from coordinator data
        grid_power = coordinator_data["plant"].get("plant_grid_sensor_active_power")

        if grid_power is None or not isinstance(grid_power, (int, float)):
            return None

        # Convert to Decimal for precise calculation
        try:
            power_dec = safe_decimal(str(grid_power))
            # Return absolute value if negative, otherwise 0
            return -power_dec if power_dec and power_dec < Decimal("0") else Decimal("0.0")
        except (ValueError, TypeError, InvalidOperation):
            # Fallback to float calculation
            return safe_decimal(-grid_power) if grid_power < 0 else Decimal("0.0")

    @staticmethod
    def calculate_plant_consumed_power(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[float]:
        """Calculate plant consumed power (household/building consumption).

        Formula: PV Power + Grid Import Power - Grid Export Power - Plant Battery Power
        """
        if coordinator_data is None or "plant" not in coordinator_data:
            return None

        # Get the required values from coordinator data
        plant_data = coordinator_data["plant"]

        total_ac_charger_power = 0.0
        ac_chargers: dict[str, Any] = coordinator_data.get("ac_chargers", {})
        for _, ac_charger_data in ac_chargers.items():
            ac_power = safe_float(ac_charger_data.get("ac_charger_charging_power"))
            if ac_power is not None:
                total_ac_charger_power += ac_power

        plant_power = plant_data.get("plant_active_power")
        grid_power = plant_data.get("plant_grid_sensor_active_power")
        third_party_pv_power = plant_data.get("plant_third_party_photovoltaic_power")

        # Validate inputs
        if plant_power is None or grid_power is None or third_party_pv_power is None:
            return None

        # Validate input types
        def are_numbers(*values):
            for x in values:
                if not isinstance(x, (int, float)):
                    try:
                        float(x)
                    except (ValueError, TypeError):
                        _LOGGER.warning(
                            "[CS][Plant Consumed] Value is not a number: %s (type: %s)",
                            x,
                            type(x).__name__,
                        )
                        return False
            return True

        if not are_numbers(grid_power, plant_power, third_party_pv_power):
            return None

        # Calculate plant consumed power
        try:
            consumed_power = max(0, float(plant_power) + float(grid_power) + float(third_party_pv_power) - total_ac_charger_power)

        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOGGER.error(
                "[CS][Plant Consumed] Error during calculation: %s", ex, exc_info=True
            )
            return None

        return consumed_power

    @staticmethod
    def _calculate_total_inverter_energy(
        coordinator_data: Optional[Dict[str, Any]],
        energy_key: str,
        log_prefix: str,
    ) -> Optional[Decimal]:
        """Helper function to calculate total energy across all inverters for a given key."""
        if coordinator_data is None or "inverters" not in coordinator_data:
            _LOGGER.debug("[%s] No inverter data available for calculation", log_prefix)
            return None

        # Check if static sensors have been initialized
        if not coordinator_data.get("_sensors_initialized", False):
            _LOGGER.debug("[%s] Static sensors not yet initialized, skipping calculation for '%s'", log_prefix, energy_key)
            return None

        total_energy = Decimal("0.0")
        inverters_data = coordinator_data.get("inverters", {})

        if not inverters_data:
            _LOGGER.debug("[%s] Inverter data is empty", log_prefix)
            return None # No inverters found

        for inverter_name, inverter_data in inverters_data.items():
            energy_value = safe_decimal(inverter_data.get(energy_key))
            if energy_value is not None:
                try:
                    total_energy += energy_value
                except (ValueError, TypeError, InvalidOperation) as e:
                    _LOGGER.warning(
                        "[%s] Invalid energy value '%s' for key '%s' in inverter %s: %s",
                        log_prefix,
                        energy_value,
                        energy_key,
                        inverter_name,
                        e
                    )
            else:
                _LOGGER.debug(
                    "[%s] Missing '%s' for inverter %s",
                    log_prefix,
                    energy_key,
                    inverter_name
                 )

        # Return as Decimal, matching other calculated sensors
        return safe_decimal(total_energy)

    @staticmethod
    def calculate_accumulated_battery_charge_energy(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate the total accumulated battery charge energy across all inverters."""
        # _LOGGER.debug("[CS][Batt Charge] Calculating accumulated battery charge energy")
        return SigenergyCalculations._calculate_total_inverter_energy(
            coordinator_data,
            "inverter_ess_accumulated_charge_energy",
            "CS][Batt Charge"
        )

    @staticmethod
    def calculate_accumulated_battery_discharge_energy(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate the total accumulated battery discharge energy across all inverters."""
        # _LOGGER.debug("[CS][Batt Discharge] Calculating accumulated battery discharge energy")
        return SigenergyCalculations._calculate_total_inverter_energy(
            coordinator_data,
            "inverter_ess_accumulated_discharge_energy",
            "CS][Batt Discharge"
        )

    @staticmethod
    def calculate_daily_battery_charge_energy(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate the total daily battery charge energy across all inverters."""
        # _LOGGER.debug("[CS][Daily Batt Charge] Calculating daily battery charge energy")
        return SigenergyCalculations._calculate_total_inverter_energy(
            coordinator_data,
            "inverter_ess_daily_charge_energy",
            "CS][Daily Batt Charge"
        )

    @staticmethod
    def calculate_daily_battery_discharge_energy(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate the total daily battery discharge energy across all inverters."""
        # _LOGGER.debug("[CS][Daily Batt Discharge] Calculating daily battery discharge energy")
        return SigenergyCalculations._calculate_total_inverter_energy(
            coordinator_data,
            "inverter_ess_daily_discharge_energy",
            "CS][Daily Batt Discharge"
        )

    @staticmethod
    def calculate_plant_daily_pv_energy(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate the total daily PV energy across all inverters."""
        # _LOGGER.debug("[CS][Daily PV] Calculating daily PV energy")
        return SigenergyCalculations._calculate_total_inverter_energy(
            coordinator_data,
            "inverter_daily_pv_energy",
            "CS][Daily PV"
        )

    @staticmethod
    def _construct_source_entity_id(
        register_name: str,
        coordinator,
        hass,
        device_type: Optional[str] = None,
        device_name: Optional[str] = None,
        pv_string_idx: Optional[int] = None,
    ) -> Optional[str]:
        """Resolve source entity via entity registry using explicit device context.

        This avoids assuming all lifetime sensors are plant-level. If device_name
        is not provided and the device_type is the plant, fall back to the
        config entry name.
        """
        from .common import get_source_entity_id
        from homeassistant.const import CONF_NAME
        from .const import DEVICE_TYPE_PLANT

        # If no explicit device_name provided and this is a plant-level sensor,
        # use the configured plant name as a fallback.
        if not device_name and device_type == DEVICE_TYPE_PLANT:
            try:
                device_name = coordinator.hub.config_entry.data.get(CONF_NAME, "Plant")
            except Exception:
                device_name = "Plant"

        return get_source_entity_id(
            device_type=device_type or DEVICE_TYPE_PLANT,
            device_name=device_name,
            source_key=register_name,
            coordinator=coordinator,
            hass=hass,
            pv_string_idx=pv_string_idx,
        )

    @staticmethod
    def calculate_daily_energy_from_lifetime(
        value,
        coordinator_data: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Decimal]:
        """Calculate daily energy from lifetime total using register name from extra_params."""
        if coordinator_data is None or "plant" not in coordinator_data:
            return None
            
        if extra_params is None or "register_name" not in extra_params:
            _LOGGER.warning("[CS][Daily Energy] Missing register_name in extra_params")
            return None

        register_name = extra_params["register_name"]
        
        # Get the current lifetime total
        current_lifetime = coordinator_data["plant"].get(register_name)
        if current_lifetime is None:
            return None

        current_lifetime_dec = safe_decimal(current_lifetime)
        if current_lifetime_dec is None:
            return None

        # The daily calculation will be handled by SigenergyLifetimeDailySensor
        # This function just returns the current lifetime value for the sensor to use
        return current_lifetime_dec


class IntegrationTrigger(Enum):
    """Trigger type for integration calculations."""

    STATE_EVENT = "state_event"
    TIME_ELAPSED = "time_elapsed"


class SigenergyLifetimeDailySensor(SigenergyEntity, RestoreSensor):
    """Sensor that calculates daily totals from lifetime values with midnight reset."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False

    def __init__(
        self,
        coordinator,
        description: SensorEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        pv_string_idx: Optional[int] = None,
    ) -> None:
        """Initialize the lifetime daily sensor."""
        # Call SigenergyEntity's __init__ first
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
        # Then initialize RestoreSensor
        RestoreSensor.__init__(self)
        
        self._attr_suggested_display_precision = getattr(
            description, "suggested_display_precision", None
        )

        # State tracking
        self._daily_value: Optional[Decimal] = None
        self._start_of_day_lifetime: Optional[Decimal] = None
        self._last_lifetime_value: Optional[Decimal] = None
        self._last_reset_date: Optional[str] = None  # Store as YYYY-MM-DD string
        
        # Sensor configuration
        self._round_digits = getattr(description, "round_digits", 6)
        self.log_this_entity = False

    def _get_current_date_str(self) -> str:
        """Get current date as YYYY-MM-DD string."""
        return dt_util.now().strftime("%Y-%m-%d")

    def _should_reset_for_new_day(self) -> bool:
        """Check if we should reset because it's a new day."""
        current_date = self._get_current_date_str()
        return self._last_reset_date != current_date

    async def _get_lifetime_value_at_midnight(self) -> Optional[Decimal]:
        """Get the lifetime value from history at the start of today.
        
        Approach:
        1. Look for a state at midnight (±30 minutes window)
        2. If not found, look for a state one hour before midnight (23:00 ±30 minutes)
        3. If not available, return None to use current reading (daily = 0 at startup)
        """
        try:
            # Get the coordinator data to determine which register to look up
            extra_params = getattr(self.entity_description, 'extra_params', None)
            if not extra_params or "register_name" not in extra_params:
                return None
            
            # Extract register name from extra_params and construct source entity ID
            register_name = extra_params.get("register_name")
            if not register_name:
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Missing register_name in extra_params: %s", 
                        self.entity_id, extra_params
                    )
                return None
            
            # Construct the source entity ID dynamically
            source_entity_id = SigenergyCalculations._construct_source_entity_id(
                register_name,
                self.coordinator,
                self.hass,
                device_type=getattr(self, "_device_type", None),
                device_name=getattr(self, "_device_name", None),
                pv_string_idx=getattr(self, "_pv_string_idx", None),
            )
            if not source_entity_id:
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Could not find source entity for register: %s", 
                        self.entity_id, register_name
                    )
                return None
            
            # Calculate midnight of current day
            now = dt_util.now()
            midnight_today = now.replace(
                hour=DAILY_RESET_HOUR, 
                minute=DAILY_RESET_MINUTE, 
                second=DAILY_RESET_SECOND, 
                microsecond=0
            )
            
            # If we're very close to midnight, look at yesterday's midnight
            if (now - midnight_today).total_seconds() < 300:  # Within 5 minutes of midnight
                midnight_today = midnight_today - timedelta(days=1)
            
            # Get recorder instance
            recorder_instance = get_instance(self.hass)
            if not recorder_instance:
                if self.log_this_entity:
                    _LOGGER.debug("[%s] Recorder not available", self.entity_id)
                return None
            
            # Primary: Look for state at midnight (±30 minutes window)
            start_time = midnight_today - timedelta(minutes=30)  # 23:30
            end_time = midnight_today + timedelta(minutes=30)    # 00:30
            
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] Looking for %s state at midnight between %s and %s", 
                    self.entity_id, source_entity_id, start_time, end_time
                )
            
            result = await self._query_history_for_midnight_value(
                recorder_instance, source_entity_id, start_time, end_time, midnight_today, "midnight"
            )
            
            if result is not None:
                return result
            
            # Fallback: Look for state around 23:00 (1 hour before midnight) - ±30 minutes window
            target_time = midnight_today - timedelta(hours=1)  # 23:00
            start_time = target_time - timedelta(minutes=30)   # 22:30
            end_time = target_time + timedelta(minutes=30)     # 23:30
            
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] No midnight value found, looking for %s state around 23:00 between %s and %s", 
                    self.entity_id, source_entity_id, start_time, end_time
                )
            
            result = await self._query_history_for_midnight_value(
                recorder_instance, source_entity_id, start_time, end_time, target_time, "23:00 fallback"
            )
            
            if result is not None:
                return result
            
            # No history found - this is fine, will use current reading (daily = 0 at startup)
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] No state found at midnight or 23:00 for %s, will use current reading", 
                    self.entity_id, source_entity_id
                )
            return None
            
        except Exception as ex:
            _LOGGER.warning(
                "[%s] Error getting midnight value from history: %s", 
                self.entity_id, ex
            )
            return None

    async def _query_history_for_midnight_value(
        self, recorder_instance, source_entity_id: str, start_time, end_time, 
        target_time, phase_name: str
    ) -> Optional[Decimal]:
        """Query history and find the state closest to the target time."""
        try:
            states_dict = await recorder_instance.async_add_executor_job(
                state_changes_during_period,
                self.hass,
                start_time,
                end_time,
                source_entity_id
            )
            
            if not states_dict or source_entity_id not in states_dict:
                return None
            
            states = states_dict[source_entity_id]
            if not states:
                return None
            
            # Find the state closest to the target time
            closest_state = None
            closest_time_diff = None
            
            for state in states:
                if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, None):
                    continue
                    
                time_diff = abs((state.last_reported - target_time).total_seconds())
                
                if closest_time_diff is None or time_diff < closest_time_diff:
                    closest_state = state
                    closest_time_diff = time_diff
            
            if closest_state is None:
                return None
            
            target_value = safe_decimal(closest_state.state)
            
            if self.log_this_entity:
                _LOGGER.info(
                    "[%s] Found %s value: %s at %s (diff: %d seconds)", 
                    self.entity_id, 
                    phase_name,
                    target_value,
                    closest_state.last_reported,
                    closest_time_diff or 0
                )
            
            return target_value
            
        except Exception as ex:
            _LOGGER.warning(
                "[%s] Error in %s query: %s", 
                self.entity_id, phase_name, ex
            )
            return None


    def _get_lifetime_value(self) -> Optional[Decimal]:
        """Get the current lifetime value from coordinator data."""
        if not hasattr(self.entity_description, 'value_fn'):
            return None
            
        try:
            # Call the value function to get the current lifetime value
            value_fn = getattr(self.entity_description, 'value_fn')
            coordinator_data = self.coordinator.data if self.coordinator else None
            
            # Get extra_fn_data flag and extra_params
            extra_fn_data = getattr(self.entity_description, 'extra_fn_data', False)
            extra_params = getattr(self.entity_description, 'extra_params', None)
            
            if extra_fn_data:
                result = value_fn(None, coordinator_data, extra_params)
            else:
                # Get the raw value from coordinator data if available
                raw_value = None
                if coordinator_data and hasattr(self.entity_description, 'key'):
                    # Try to get value from plant data first
                    plant_data = coordinator_data.get("plant", {})
                    raw_value = plant_data.get(self.entity_description.key)
                
                result = value_fn(raw_value)
            
            return safe_decimal(result) if result is not None else None
            
        except Exception as ex:
            _LOGGER.warning(
                "[%s] Error getting lifetime value: %s", 
                self.entity_id, ex
            )
            return None

    def _reset_daily_calculation(self, current_lifetime: Decimal) -> None:
        """Reset the daily calculation for a new day."""
        self._start_of_day_lifetime = current_lifetime
        self._daily_value = Decimal("0.0")
        self._last_reset_date = self._get_current_date_str()
        
        if self.log_this_entity:
            _LOGGER.debug(
                "[%s] Reset for new day: start_of_day=%s, date=%s",
                self.entity_id,
                self._start_of_day_lifetime,
                self._last_reset_date
            )

    def _calculate_daily_value(self, current_lifetime: Decimal) -> Optional[Decimal]:
        """Calculate the daily value from current and start-of-day lifetime values."""
        if self._start_of_day_lifetime is None:
            # First time - we'll set this up properly in async_added_to_hass
            # For now, just return None to indicate we're not ready yet
            return None
        
        # Check if we need to reset for a new day
        if self._should_reset_for_new_day():
            self._reset_daily_calculation(current_lifetime)
            return Decimal("0.0")
        
        # Handle potential counter rollover (rare but possible)
        if current_lifetime < self._start_of_day_lifetime:
            _LOGGER.warning(
                "[%s] Lifetime counter rollover detected: current=%s < start_of_day=%s",
                self.entity_id,
                current_lifetime,
                self._start_of_day_lifetime
            )
            # Reset with current value as new start
            self._reset_daily_calculation(current_lifetime)
            return Decimal("0.0")
        
        # Normal calculation
        daily_value = current_lifetime - self._start_of_day_lifetime
        
        if self.log_this_entity:
            _LOGGER.debug(
                "[%s] Daily calculation: %s = %s - %s",
                self.entity_id,
                daily_value,
                current_lifetime,
                self._start_of_day_lifetime
            )
        
        return daily_value

    def _update_from_coordinator(self) -> None:
        """Update sensor value from coordinator data."""
        current_lifetime = self._get_lifetime_value()
        
        if current_lifetime is None:
            if self.log_this_entity:
                _LOGGER.debug("[%s] No lifetime value available", self.entity_id)
            return
        
        # Calculate daily value (will return None if not initialized yet)
        daily_value = self._calculate_daily_value(current_lifetime)
        
        if daily_value is not None:
            self._daily_value = daily_value
            self._last_lifetime_value = current_lifetime
            
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] Updated: daily=%s, lifetime=%s",
                    self.entity_id,
                    self._daily_value,
                    current_lifetime
                )
        elif self.log_this_entity:
            _LOGGER.debug(
                "[%s] Not ready for calculation yet (start_of_day not set)",
                self.entity_id
            )

    def _setup_midnight_reset(self) -> None:
        """Schedule reset at midnight."""
        now = dt_util.now()
        # Calculate next midnight
        next_midnight = (now + timedelta(days=1)).replace(
            hour=DAILY_RESET_HOUR, 
            minute=DAILY_RESET_MINUTE, 
            second=DAILY_RESET_SECOND, 
            microsecond=0
        )

        @callback
        def _handle_midnight(current_time):
            """Handle midnight reset."""
            if self.log_this_entity:
                _LOGGER.debug("[%s] Midnight reset triggered at %s", self.entity_id, current_time)
            
            # Get current lifetime value and reset
            current_lifetime = self._get_lifetime_value()
            if current_lifetime is not None:
                self._reset_daily_calculation(current_lifetime)
                self.async_write_ha_state()
            
            # Schedule next reset
            self._setup_midnight_reset()

        # Schedule the reset
        self.async_on_remove(
            async_track_point_in_time(self.hass, _handle_midnight, next_midnight)
        )
        
        if self.log_this_entity:
            _LOGGER.debug(
                "[%s] Scheduled midnight reset for %s", 
                self.entity_id, 
                next_midnight
            )

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        self.log_this_entity = self.entity_id in LOG_THIS_ENTITY

        # Try to restore previous state
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                # Restore daily value
                self._daily_value = safe_decimal(last_state.state)
                
                # Restore attributes
                if last_state.attributes:
                    start_of_day = last_state.attributes.get("start_of_day_lifetime")
                    if start_of_day is not None:
                        self._start_of_day_lifetime = safe_decimal(start_of_day)
                    
                    last_reset = last_state.attributes.get("last_reset_date")
                    if last_reset:
                        self._last_reset_date = last_reset
                
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Restored state: daily=%s, start_of_day=%s, last_reset=%s",
                        self.entity_id,
                        self._daily_value,
                        self._start_of_day_lifetime,
                        self._last_reset_date
                    )
                        
            except (ValueError, TypeError, InvalidOperation) as e:
                _LOGGER.warning(
                    "[%s] Could not restore state: %s", 
                    self.entity_id, e
                )

        # Check if midnight has passed since last update or if we need to initialize start-of-day
        current_lifetime = self._get_lifetime_value()
        
        if self._should_reset_for_new_day():
            if current_lifetime is not None:
                self._reset_daily_calculation(current_lifetime)
                if self.log_this_entity:
                    _LOGGER.info(
                        "[%s] Reset due to date change on startup", 
                        self.entity_id
                    )
        elif self._start_of_day_lifetime is None and current_lifetime is not None:
            # First time setup - try to get the start-of-day value from history
            try:
                midnight_value = await self._get_lifetime_value_at_midnight()
                if midnight_value is not None:
                    self._start_of_day_lifetime = midnight_value
                    self._last_reset_date = self._get_current_date_str()
                    # Calculate initial daily value
                    if current_lifetime >= midnight_value:
                        self._daily_value = current_lifetime - midnight_value
                    else:
                        # Handle potential rollover
                        _LOGGER.warning(
                            "[%s] Current lifetime (%s) < midnight value (%s), treating as rollover",
                            self.entity_id, current_lifetime, midnight_value
                        )
                        self._daily_value = Decimal("0.0")
                        self._start_of_day_lifetime = current_lifetime
                    
                    if self.log_this_entity:
                        _LOGGER.info(
                            "[%s] Initialized from history: start_of_day=%s, current=%s, daily=%s", 
                            self.entity_id, 
                            self._start_of_day_lifetime,
                            current_lifetime,
                            self._daily_value
                        )
                else:
                    # Fallback to current value as start of day
                    self._reset_daily_calculation(current_lifetime)
                    if self.log_this_entity:
                        _LOGGER.info(
                            "[%s] No history found, using current value as start-of-day: %s", 
                            self.entity_id, current_lifetime
                        )
                        
            except Exception as ex:
                _LOGGER.warning(
                    "[%s] Error during history initialization, using current value: %s", 
                    self.entity_id, ex
                )
                self._reset_daily_calculation(current_lifetime)

        # Set up midnight reset scheduling
        self._setup_midnight_reset()

        # Update from coordinator data initially
        self._update_from_coordinator()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_coordinator()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> Decimal | None:
        """Return the state of the sensor."""
        if self._daily_value is None:
            return None
        return round(self._daily_value, self._round_digits)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {
            "last_reset_date": self._last_reset_date,
        }
        
        if self._start_of_day_lifetime is not None:
            attrs["start_of_day_lifetime"] = str(self._start_of_day_lifetime)
            
        if self._last_lifetime_value is not None:
            attrs["current_lifetime"] = str(self._last_lifetime_value)
            
        return attrs


class SigenergyIntegrationSensor(SigenergyEntity, RestoreSensor):
    """Implementation of an Integration Sensor with identical behavior to HA core."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator,
        description: SensorEntityDescription,
        name: str,
        device_type: str,
        device_id: Optional[str] = None,
        device_name: str = "",
        device_info: Optional[DeviceInfo] = None,
        source_entity_id: str = "",
        pv_string_idx: Optional[int] = None,
    ) -> None:
        # Initialize state variables
        self._state: Decimal | None = None
        self._last_valid_state: Decimal | None = None

        """Initialize the integration sensor."""
        # Call SigenergyEntity's __init__ first
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
        # Then initialize RestoreSensor
        RestoreSensor.__init__(self)
        self._attr_suggested_display_precision = getattr(
            description, "suggested_display_precision", None
        )

        # Sensor-specific initialization
        self._source_entity_id = source_entity_id
        self._round_digits = getattr(description, "round_digits", 6)
        self._max_sub_interval = getattr(description, "max_sub_interval", timedelta(seconds=30))
        self.log_this_entity = False
        self._last_coordinator_update = None

        # Time tracking variables
        self._max_sub_interval_exceeded_callback = lambda *args: None  # Just a placeholder
        self._cancel_max_sub_interval_exceeded_callback = None  # Will store the cancel handle
        self._last_integration_time = dt_util.utcnow()
        self._last_integration_trigger = IntegrationTrigger.STATE_EVENT

        # Device info is now handled by SigenergyEntity's __init__

    def _decimal_state(self, state: str) -> Optional[Decimal]:
        """Convert state to Decimal or return None if not possible."""
        try:
            return safe_decimal(state)
        except (InvalidOperation, TypeError) as e:
            _LOGGER.warning("[CS][State] Failed to convert %s to Decimal: %s", state, e)
            return None

    def _validate_states(
        self, left: str, right: str
    ) -> Optional[tuple[Decimal, Decimal]]:
        """Validate states and convert to Decimal."""
        if (left_dec := self._decimal_state(left)) is None or (
            right_dec := self._decimal_state(right)
        ) is None:
            return None
        return (left_dec, right_dec)

    def _calculate_trapezoidal(
        self, elapsed_time: Decimal, left: Decimal, right: Decimal
    ) -> Decimal:
        """Calculate area using the trapezoidal method."""
        return elapsed_time * (left + right) / Decimal(2)

    def _update_integral(self, area: Decimal) -> None:
        """Update the integral with the calculated area."""
        state_before = self._state
        # Convert seconds to hours
        area_scaled = area / Decimal(3600)

        if isinstance(self._state, Decimal):
            self._state += area_scaled
        else:
            self._state = area_scaled

        if self.log_this_entity:
            _LOGGER.debug(
                "[%s] _update_integral - Area: %s, State before: %s, State after: %s",
                self.entity_id,
                area_scaled,
                state_before,
                self._state,
            )
            _LOGGER.debug(
                "[%s] _update_integral - Area before scale: %s, Area after scale: %s",
                self.entity_id, area, area_scaled
            )

        # Only update last_valid_state if we have a valid calculation
        if self._state is not None and isinstance(self._state, Decimal):
            # We only want to save positive values
            if self._state >= Decimal('0'):
                self._last_valid_state = self._state
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] _update_integral - Updated _last_valid_state: %s (state_class: %s)",
                        self.entity_id,
                        self._last_valid_state,
                        self.state_class
                    )

    def _setup_midnight_reset(self) -> None:
        """Schedule reset at midnight."""
        now = dt_util.now()
        # Calculate last second of the current day (23:59:59)
        midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
        # If we're already past midnight, use tomorrow's date
        if now.hour >= 23 and now.minute >= 59 and now.second >= 59:
            midnight = (now + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)

        @callback
        def _handle_midnight(current_time):
            """Handle midnight reset."""
            state_before = self._state
            self._state = Decimal(0)
            self._last_valid_state = self._state
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] _handle_midnight - Resetting state from %s to 0",
                    self.entity_id,
                    state_before,
                )
            self.async_write_ha_state()
            if self.log_this_entity:
                _LOGGER.debug("[%s] _handle_midnight - Called async_write_ha_state()",
                               self.entity_id)
            self._setup_midnight_reset()  # Schedule next reset

        # Schedule the reset
        self.async_on_remove(
            async_track_point_in_time(self.hass, _handle_midnight, midnight)
        )

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        self.log_this_entity = self.entity_id in LOG_THIS_ENTITY
        restore_value = None
        restored_from_config = False  # Flag to track if value came from config

        # Check if there is qued restore for this value either migration or manual reset.
        config_entry = self.hub.config_entry
        if config_entry:
            # Use .get() with a default empty dict to avoid potential KeyError
            _resetting_sensors = config_entry.data.get(CONF_VALUES_TO_INIT, {})

            if self.entity_id in _resetting_sensors:
                _LOGGER.debug("Sensor %s is in the list of restorable sensors", self.entity_id)
                init_value = _resetting_sensors.get(self.entity_id) # Use .get() for safety
                if init_value is not None and init_value not in (
                    STATE_UNKNOWN,
                    STATE_UNAVAILABLE,
                    ""
                ):
                    # Convert to Decimal safely
                    init_value_dec = safe_decimal(init_value)
                    if init_value_dec is not None:
                        restore_value = init_value_dec
                        if self.log_this_entity:
                            _LOGGER.info("Saving initial value for %s: %s", self.entity_id, restore_value)
                        restored_from_config = True # Mark that we restored from config
                    else:
                        _LOGGER.warning("Could not convert init_value '%s' to Decimal for %s", init_value, self.entity_id)
                        restore_value = None # Ensure restore_value is None if conversion fails
                        restored_from_config = False # Do not mark as restored if conversion failed

                    # Remove the entity from list of restorable
                    # Create a mutable copy before modifying
                    mutable_resetting_sensors = dict(_resetting_sensors)
                    mutable_resetting_sensors.pop(self.entity_id, None) # Use pop with default None

                    # Make new Configuration data from original
                    new_config_data = dict(config_entry.data)
                    new_config_data[CONF_VALUES_TO_INIT] = mutable_resetting_sensors

                    # Update the plant's configuration with the new data
                    self.hass.config_entries.async_update_entry(config_entry, data=new_config_data)

        # Only check last_state if we haven't restored from config yet
        if not restored_from_config:
            # Restore previous state if available
            last_state = await self.async_get_last_state()
            if last_state and last_state.state not in (
                None,
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            ):
                if self.unit_of_measurement == "MWh":
                    restore_value = str(Decimal(last_state.state) * 1000)
                else:
                    restore_value = str(Decimal(last_state.state) * 1)
                if self.log_this_entity:
                    if self.unit_of_measurement == last_state.attributes["unit_of_measurement"]:
                        _LOGGER.debug("Both are %s", self.unit_of_measurement)
                    else:
                        _LOGGER.debug("Self is %s and last is %s", self.unit_of_measurement, last_state.attributes["unit_of_measurement"])

            else:
                _LOGGER.debug(
                    "No valid last state available for %s, using default value",
                    self.entity_id,
                )

        if restore_value is not None: # Check if restore_value is not None before trying to use it
            try:
                # Ensure restore_value is converted to string before passing to safe_decimal
                restored_state = safe_decimal(str(restore_value))
                # Check if conversion was successful and resulted in a Decimal
                if isinstance(restored_state, Decimal):
                    self._state = restored_state
                    self._last_valid_state = self._state
                    self._last_integration_time = dt_util.utcnow()
                else:
                    _LOGGER.warning("Could not convert restore value '%s' to Decimal for %s", restore_value, self.entity_id)
                    # Try to use last_valid_state if available as fallback
                    if self._last_valid_state is not None:
                        self._state = self._last_valid_state
                        _LOGGER.debug("Falling back to last valid state for %s: %s", self.entity_id, self._last_valid_state)
            except (ValueError, TypeError, InvalidOperation) as e:
                _LOGGER.warning("Could not restore state for %s from value '%s': %s", self.entity_id, restore_value, e)
                # Try to use last_valid_state if available as fallback 
                if self._last_valid_state is not None:
                    self._state = self._last_valid_state
                    _LOGGER.debug("Falling back to last valid state for %s: %s", self.entity_id, self._last_valid_state)
        elif self._last_valid_state is not None:
            # If no restore value but we have a last valid state, use that
            self._state = self._last_valid_state
            _LOGGER.debug("Using last valid state for %s: %s", self.entity_id, self._last_valid_state)
        else:
            _LOGGER.debug("No restore value available for %s, state remains uninitialized.", self.entity_id)

        # Set up appropriate handlers based on max_sub_interval
        # Ensure source_entity_id is valid before proceeding
        if not self._source_entity_id:
            _LOGGER.error(
                "Source entity ID is not a valid string for %s: %s",
                self.entity_id,
                self._source_entity_id,
            )
            return  # Cannot set up tracking without a valid source ID

        if self._max_sub_interval is not None:
            source_state = self.hass.states.get(self._source_entity_id)
            self._schedule_max_sub_interval_exceeded_if_state_is_numeric(source_state)
            handle_state_change = self._integrate_on_state_change_with_max_sub_interval
        else:
            if self.log_this_entity:
                _LOGGER.debug(
                    "No max_sub_interval set, using default state change handler for %s",
                    self.name
                )
            handle_state_change = self._integrate_on_state_change_callback

        # Set up midnight reset for daily sensors
        if "daily" in self.entity_description.key:
            self._setup_midnight_reset()

        # Register to track source sensor state changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._source_entity_id],
                handle_state_change,
                # Use the checked source_entity_id
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        """Handle entity removal."""
        # Cancel any scheduled timers
        if self._cancel_max_sub_interval_exceeded_callback is not None:
            # Only log for specific entities
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] Cancelling timer on entity removal", self.entity_id
                )
            self._cancel_max_sub_interval_exceeded_callback()
            self._cancel_max_sub_interval_exceeded_callback = None
        await super().async_will_remove_from_hass()

    @callback
    def _integrate_on_state_change_callback(self, event) -> None:
        """Handle sensor state change without max_sub_interval."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        self._integrate_on_state_change(old_state, new_state)

    @callback
    def _integrate_on_state_change_with_max_sub_interval(self, event) -> None:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")

        # Cancel existing timer safely
        if self._cancel_max_sub_interval_exceeded_callback is not None:
            self._cancel_max_sub_interval_exceeded_callback()
            self._cancel_max_sub_interval_exceeded_callback = None

        now = dt_util.utcnow()
        time_since_last = (now - self._last_integration_time).total_seconds()

        if time_since_last < DEFAULT_MIN_INTEGRATION_TIME:
            if self.log_this_entity:
                _LOGGER.debug("Skipping integration for %s, interval too short: %.2fs", self.name, time_since_last)
            return

        try:
            self._integrate_on_state_change(old_state, new_state)
            self._last_integration_trigger = IntegrationTrigger.STATE_EVENT
            self._last_integration_time = now
        except Exception as ex:
            _LOGGER.warning("Integration error for %s: %s", self.entity_id, ex)
        finally:
            # Reschedule timer after processing state change
            self._schedule_max_sub_interval_exceeded_if_state_is_numeric(new_state)

    def _integrate_on_state_change(
        self, old_state: State | None, new_state: State | None
    ) -> None:
        """Perform integration based on state change."""
        if self.log_this_entity:
            _LOGGER.debug("[_integrate_on_state_change] Starting for %s with old_state: %s, new_state: %s",
                          self.entity_id, old_state, new_state)
        if new_state is None:
            return

        if old_state is None or old_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        # Validate states
        if not (states := self._validate_states(old_state.state, new_state.state)):
            return

        # Calculate elapsed time
        elapsed_seconds = Decimal(
            (new_state.last_reported - old_state.last_reported).total_seconds()
            if self._last_integration_trigger == IntegrationTrigger.STATE_EVENT
            else (new_state.last_reported - self._last_integration_time).total_seconds()
        )

        # Calculate area
        area = self._calculate_trapezoidal(elapsed_seconds, *states)
        if self.log_this_entity:
            _LOGGER.debug(
                "[%s] _integrate_on_state_change - Calculated area: %s",
                self.entity_id,
                area,
            )

        # Update the integral
        self._update_integral(area)

        # Write state
        if self.log_this_entity:
            _LOGGER.debug(
                "[%s] _integrate_on_state_change - Calling async_write_ha_state() with state: %s",
                self.entity_id,
                self._state,
            )
        self.async_write_ha_state()

    def _schedule_max_sub_interval_exceeded_if_state_is_numeric(
        self, source_state: State | None
    ) -> None:
        """Schedule integration based on max_sub_interval."""
        if (
            self._max_sub_interval is not None
            and source_state is not None
            and (source_state_dec := self._decimal_state(source_state.state))
            is not None
        ):

            @callback
            def _integrate_on_max_sub_interval_exceeded_callback(now: datetime) -> None:
                """Integrate based on time and reschedule."""
                if self.log_this_entity:
                    _LOGGER.debug("[%s] Timer callback executed at %s", self.entity_id, now)

                time_since_last = now - self._last_integration_time
                if self._last_integration_trigger == IntegrationTrigger.STATE_EVENT and time_since_last < timedelta(seconds=5):
                    if self.log_this_entity:
                        _LOGGER.debug(
                            "[%s] Skipping timer integration; state change occurred %s ago. Rescheduling only.",
                            self.entity_id, time_since_last
                        )
                    self._schedule_max_sub_interval_exceeded_if_state_is_numeric(self.hass.states.get(self._source_entity_id))
                    return

                elapsed_seconds = safe_decimal((now - self._last_integration_time).total_seconds())
                if not elapsed_seconds:
                    return

                try:
                    area = elapsed_seconds * source_state_dec
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "[%s] Timer - Error calculating area: %s", self.entity_id, e
                    )
                    return

                self._update_integral(area)
                self.async_write_ha_state()

                self._last_integration_time = now
                self._last_integration_trigger = IntegrationTrigger.TIME_ELAPSED
                self._schedule_max_sub_interval_exceeded_if_state_is_numeric(source_state)

            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] Scheduling timer with interval %s", self.entity_id, self._max_sub_interval
                )
            self._cancel_max_sub_interval_exceeded_callback = async_call_later(
                self.hass,
                self._max_sub_interval,
                _integrate_on_max_sub_interval_exceeded_callback,
            )

    @property
    def native_value(self) -> Decimal | None:
        """Return the state of the sensor."""
        if self._state is None:
            return None
        return round(self._state, self._round_digits)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        return {
            "source_entity": self._source_entity_id,
        }

class SigenergyCalculatedSensors:
    """Class for holding calculated sensor methods."""

    # Lifetime-based daily energy sensors (require special handling)
    PLANT_LIFETIME_DAILY_SENSORS = [
        SigenergySensorEntityDescription(
            key="plant_daily_grid_import_energy",
            name="Daily Grid Import Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            # 'tower-export' icon means 'energy from grid'
            icon="mdi:transmission-tower-export",
            value_fn=SigenergyCalculations.calculate_daily_energy_from_lifetime,
            extra_fn_data=True,
            extra_params={
                "register_name": "plant_accumulated_grid_import_energy",
            },
            suggested_display_precision=2,
            round_digits=6,
        ),
        SigenergySensorEntityDescription(
            key="plant_daily_grid_export_energy",
            name="Daily Grid Export Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            # 'tower-import' icon means 'energy to grid'
            icon="mdi:transmission-tower-import",
            value_fn=SigenergyCalculations.calculate_daily_energy_from_lifetime,
            extra_fn_data=True,
            extra_params={
                "register_name": "plant_accumulated_grid_export_energy",
            },
            suggested_display_precision=2,
            round_digits=6,
        ),
        SigenergySensorEntityDescription(
            key="plant_daily_third_party_inverter_energy_from_lifetime",
            name="Daily Third Party Inverter Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING,
            icon="mdi:home-lightning-bolt",
            value_fn=SigenergyCalculations.calculate_daily_energy_from_lifetime,
            extra_fn_data=True,
            extra_params={
                "register_name": "plant_total_generation_of_third_party_inverter",
            },
            suggested_display_precision=2,
            round_digits=6,
        ),
    ]

    PV_STRING_SENSORS = [
        SigenergySensorEntityDescription(
            key="power",
            name="Power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            state_class=SensorStateClass.MEASUREMENT,
            value_fn=SigenergyCalculations.calculate_pv_power,
            extra_fn_data=True,
            suggested_display_precision=3,
            round_digits=6,
            icon="mdi:solar-power",
        ),
    ]

    PLANT_SENSORS = [
        # System time and timezone
        SigenergySensorEntityDescription(
            key="plant_system_time",
            name="System Time",
            icon="mdi:clock",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            # Adapt function signature to match expected value_fn
            value_fn=lambda value, coord_data, _: SigenergyCalculations.epoch_to_datetime(
                value, coord_data
            ),
            extra_fn_data=True,  # Indicates that this sensor needs coordinator data
            entity_registry_enabled_default=False,
        ),
        SigenergySensorEntityDescription(
            key="plant_system_timezone",
            name="System Timezone",
            icon="mdi:earth",
            entity_category=EntityCategory.DIAGNOSTIC,
            # Adapt function signature
            value_fn=lambda value, _, __: SigenergyCalculations.minutes_to_gmt(value),
            entity_registry_enabled_default=False,
        ),
        # EMS Work Mode sensor with value mapping
        SigenergySensorEntityDescription(
            key="plant_ems_work_mode",
            name="EMS Work Mode",
            icon="mdi:home-battery",
            # Adapt function signature
            value_fn=lambda value, _, __: {
                EMSWorkMode.MAX_SELF_CONSUMPTION: "Maximum Self Consumption",
                EMSWorkMode.AI_MODE: "AI Mode",
                EMSWorkMode.TOU: "Time of Use",
                EMSWorkMode.FULL_FEED_IN_TO_GRID: "Full Feed-In to Grid",
                EMSWorkMode.VPP_SCHEDULING: "VPP Scheduling",
                EMSWorkMode.REMOTE_EMS: "Remote EMS",
                EMSWorkMode.CUSTOM: "Custom",
            }.get(value, f"Unknown: ({value})"), # Fallback to original value
        ),
        SigenergySensorEntityDescription(
            key="plant_photovoltaic_power",
            name="PV Power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:solar-power",
            value_fn=SigenergyCalculations.calculate_total_pv_power,
            extra_fn_data=True,  # Pass coordinator data to value_fn
            suggested_display_precision=3,
            round_digits=6,
        ),
        SigenergySensorEntityDescription(
            key="plant_grid_import_power",
            name="Grid Import Power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:transmission-tower-export",
            value_fn=SigenergyCalculations.calculate_grid_import_power,
            extra_fn_data=True,  # Pass coordinator data to value_fn
            suggested_display_precision=3,
            round_digits=6,
        ),
        SigenergySensorEntityDescription(
            key="plant_grid_export_power",
            name="Grid Export Power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:transmission-tower-import",
            value_fn=SigenergyCalculations.calculate_grid_export_power,
            extra_fn_data=True,  # Pass coordinator data to value_fn
            suggested_display_precision=3,
            round_digits=6,
        ),
        SigenergySensorEntityDescription(
            key="plant_consumed_power",
            name="Consumed Power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:home-lightning-bolt",
            value_fn=SigenergyCalculations.calculate_plant_consumed_power,
            extra_fn_data=True,  # Pass coordinator data to value_fn
            suggested_display_precision=3,
            round_digits=6,
        ),
        SigenergySensorEntityDescription(
            key="plant_daily_pv_energy",
            name="Daily PV Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=SigenergyCalculations.calculate_plant_daily_pv_energy,
            extra_fn_data=True,  # Pass coordinator data to value_fn
            max_sub_interval=timedelta(seconds=30),
            icon="mdi:solar-power",
        ),
        # SigenergySensorEntityDescription(
        #     key="plant_accumulated_battery_charge_energy",
        #     name="Accumulated Battery Charge Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     state_class=SensorStateClass.TOTAL, # Assumes this value only increases
        #     icon="mdi:battery-positive",
        #     value_fn=SigenergyCalculations.calculate_accumulated_battery_charge_energy,
        #     extra_fn_data=True, # Pass coordinator data to value_fn
        #     suggested_display_precision=3,
        #     round_digits=6, # Match other energy sensors
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR # Suggest a different unit for display
        # ),
        # SigenergySensorEntityDescription(
        #     key="plant_accumulated_battery_discharge_energy",
        #     name="Accumulated Battery Discharge Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     state_class=SensorStateClass.TOTAL, # Assumes this value only increases
        #     icon="mdi:battery-negative",
        #     value_fn=SigenergyCalculations.calculate_accumulated_battery_discharge_energy,
        #     extra_fn_data=True, # Pass coordinator data to value_fn
        #     suggested_display_precision=3,
        #     round_digits=6, # Match other energy sensors
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR # Suggest a different unit for display
        # ),
        SigenergySensorEntityDescription(
            key="plant_daily_battery_charge_energy",
            name="Daily Battery Charge Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING, # Resets daily
            icon="mdi:battery-positive",
            value_fn=SigenergyCalculations.calculate_daily_battery_charge_energy,
            extra_fn_data=True, # Pass coordinator data to value_fn
            suggested_display_precision=2,
            round_digits=6, # Match other energy sensors
        ),
        SigenergySensorEntityDescription(
            key="plant_daily_battery_discharge_energy",
            name="Daily Battery Discharge Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            state_class=SensorStateClass.TOTAL_INCREASING, # Resets daily
            icon="mdi:battery-negative",
            value_fn=SigenergyCalculations.calculate_daily_battery_discharge_energy,
            extra_fn_data=True, # Pass coordinator data to value_fn
            suggested_display_precision=2,
            round_digits=6, # Match other energy sensors
        ),
    ]

    INVERTER_SENSORS = [
        SigenergySensorEntityDescription(
            key="inverter_startup_time",
            name="Startup Time",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            # Adapt function signature
            value_fn=lambda value, coord_data, _: SigenergyCalculations.epoch_to_datetime(
                value, coord_data
            ),
            extra_fn_data=True,  # Indicates that this sensor needs coordinator data
            entity_registry_enabled_default=False,
        ),
        SigenergySensorEntityDescription(
            key="inverter_shutdown_time",
            name="Shutdown Time",
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_category=EntityCategory.DIAGNOSTIC,
            # Adapt function signature
            value_fn=lambda value, coord_data, _: SigenergyCalculations.epoch_to_datetime(
                value, coord_data
            ),
            extra_fn_data=True,  # Indicates that this sensor needs coordinator data
            entity_registry_enabled_default=False,
        ),
    ]

    AC_CHARGER_SENSORS = []

    DC_CHARGER_SENSORS = []

    # Add the plant integration sensors list
    PLANT_INTEGRATION_SENSORS = [
    ]

    # Add the inverter integration sensors list
    INVERTER_INTEGRATION_SENSORS = [
    ]
    # Integration sensors for individual PV strings (dynamically created)
    PV_INTEGRATION_SENSORS = [
        SigenergySensorEntityDescription(
            key="pv_string_accumulated_energy", # Template key
            name="Accumulated Energy", # Template name
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            state_class=SensorStateClass.TOTAL,
            # Source entity ID (e.g., sensor.sigen_inverter_XYZ_pv1_power)
            # will be dynamically constructed in sensor.py using device_name and pv_idx.
            # This source_key identifies the *type* of source.
            source_key="pv_string_power",
            round_digits=6,
            max_sub_interval=timedelta(seconds=30),
            icon="mdi:solar-power",
            suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR
        ),
        SigenergySensorEntityDescription(
            key="pv_string_daily_energy", # Template key
            name="Daily Energy", # Template name
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            state_class=SensorStateClass.TOTAL_INCREASING, # Resets daily
            # Source entity ID constructed dynamically in sensor.py
            source_key="pv_string_power",
            round_digits=6,
            max_sub_interval=timedelta(seconds=30),
            icon="mdi:solar-power",
        ),
    ]

