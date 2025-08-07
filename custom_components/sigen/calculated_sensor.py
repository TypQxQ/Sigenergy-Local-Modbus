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
        # _LOGGER.debug("AC Chargers data: %s", ac_chargers)
        for _, ac_charger_data in ac_chargers.items():
            ac_power = safe_float(ac_charger_data.get("ac_charger_charging_power"))
            if ac_power is not None:
                total_ac_charger_power += ac_power

        total_dc_charger_power = 0.0
        dc_chargers: dict[str, Any] = coordinator_data.get("dc_chargers", {})
        # _LOGGER.debug("DC Chargers data: %s", dc_chargers)
        for _, dc_charger_data in dc_chargers.items():
            dc_power = safe_float(dc_charger_data.get("dc_charger_output_power"))
            if dc_power is not None:
                total_dc_charger_power += dc_power

        # Use the correct calculation for total PV power
        pv_power = SigenergyCalculations.calculate_total_pv_power(
            None, coordinator_data=coordinator_data
        )

        grid_power = plant_data.get("plant_grid_sensor_active_power")

        # Get battery power
        battery_power = plant_data.get("plant_ess_power")

        # Validate inputs
        if pv_power is None or grid_power is None or battery_power is None:
            return None

        # Validate input types
        if not isinstance(pv_power, (int, float)):
            _LOGGER.warning(
                "[CS][Plant Consumed] PV power is not a number: %s (type: %s)",
                pv_power,
                type(pv_power).__name__,
            )
            return None
        if not isinstance(grid_power, (int, float)):
            _LOGGER.warning(
                "[CS][Plant Consumed] Grid power is not a number: %s (type: %s)",
                grid_power,
                type(grid_power).__name__,
            )
            return None
        if not isinstance(battery_power, (int, float)):
            _LOGGER.warning(
                "[CS][Plant Consumed] Battery power is not a number: %s (type: %s)",
                battery_power,
                type(battery_power).__name__,
            )
            return None

        # Calculate grid import and export power
        # Grid power is positive when importing, negative when exporting
        grid_import = max(0, grid_power)
        grid_export = max(0, -grid_power)

        # Calculate plant consumed power
        # Note: battery_power is positive when charging, negative when discharging
        try:
            consumed_power = pv_power + grid_import - grid_export - battery_power - total_ac_charger_power - total_dc_charger_power

            # Sanity check
            if consumed_power < 0:
                _LOGGER.warning(
                    "[CS][Plant Consumed] Calculated power is negative.\n" \
                    "consumed_power = pv_power + grid_import - grid_export - battery_power - total_ac_charger_power - total_dc_charger_power:\n" \
                    "%s kW = %s + %s - %s - %s - %s - %s",
                    consumed_power,
                    pv_power,
                    grid_import,
                    grid_export,
                    battery_power,
                    total_ac_charger_power,
                    total_dc_charger_power
                )
                # Keep the negative value as it might be valid in some scenarios

            if consumed_power > 50:  # Unlikely to have consumption over 50 kW
                _LOGGER.warning(
                    "[CS][Plant Consumed] Calculated power seems excessive: %s kW",
                    consumed_power,
                )
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


class IntegrationTrigger(Enum):
    """Trigger type for integration calculations."""

    STATE_EVENT = "state_event"
    TIME_ELAPSED = "time_elapsed"


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
        device_name: Optional[str] = "",
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
        self._round_digits = getattr(description, "round_digits", None)
        self._max_sub_interval = getattr(description, "max_sub_interval", None)
        self.log_this_entity = False
        self._last_coordinator_update = None

        # Time tracking variables
        self._max_sub_interval = (
            None  # disable time based integration
            if self._max_sub_interval is None
            or self._max_sub_interval.total_seconds() == 0
            else self._max_sub_interval
        )

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
            # Only log for specific entities
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] Cancelling timer due to state change", self.entity_id
                )
            self._cancel_max_sub_interval_exceeded_callback()
            self._cancel_max_sub_interval_exceeded_callback = None

        now = dt_util.utcnow()
        # Compare coordinator update time and elapsed interval
        coordinatorTime = self.coordinator.last_update_success or now
        timeSinceLast = (now - self._last_integration_time).total_seconds()
        if coordinatorTime == getattr(self, "_last_coordinator_update", None) \
           and timeSinceLast < DEFAULT_MIN_INTEGRATION_TIME:
            _LOGGER.debug("Skipping integration: %s, interval too short: %s", self.name, timeSinceLast)
        else:
            self._last_coordinator_update = coordinatorTime
            try:
                self._integrate_on_state_change(old_state, new_state)
                self._last_integration_trigger = IntegrationTrigger.STATE_EVENT
                self._last_integration_time = now
                # _LOGGER.debug(f"[_integrate_on_state_change_with_max_sub_interval] Setting _last_integration_time: {self._last_integration_time.time()}")
            except Exception as ex:
                _LOGGER.warning("Integration error: %s", ex)
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
            # Only log scheduling for specific entities

            @callback
            def _integrate_on_max_sub_interval_exceeded_callback(now: datetime) -> None:
                """Integrate based on time and reschedule."""
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Timer callback executed at %s", self.entity_id, now
                    )
                # ... existing checks ...

                # Check if a state change happened very recently to avoid double updates
                time_since_last = now - self._last_integration_time
                # Use fixed buffer of 5 seconds
                if self._last_integration_trigger == IntegrationTrigger.STATE_EVENT and time_since_last < timedelta(seconds=5):
                    if self.log_this_entity:
                        _LOGGER.debug(
                            "[%s] Skipping timer integration; state change occurred %s ago. Rescheduling only.",
                            self.entity_id,
                            time_since_last,
                        )
                    # Only reschedule the next integration
                    source_state_obj = self.hass.states.get(self._source_entity_id)
                    if source_state_obj: # Ensure state object exists before rescheduling
                        self._schedule_max_sub_interval_exceeded_if_state_is_numeric(
                            source_state_obj)
                    return

                if self.log_this_entity:
                    _LOGGER.debug("[%s] Performing timer-based integration", self.entity_id)

                elapsed_seconds = safe_decimal(
                    (now - self._last_integration_time).total_seconds()
                )
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Timer - Elapsed seconds: %s, Last state decimal: %s",
                        self.entity_id,
                        elapsed_seconds,
                        source_state_dec, # Log the state value used
                    )

                # Calculate area with constant state
                try:
                    if elapsed_seconds and source_state:
                        area = elapsed_seconds * source_state_dec
                    else:
                        raise ValueError(
                            "Elapsed seconds or source state is invalid for area calculation"
                        )
                except (ValueError, TypeError) as e:
                    _LOGGER.warning(
                        "[%s] Timer - Error calculating area. elapsed_seconds: %s, state: %s, error: %s",
                        self.entity_id,
                        elapsed_seconds,
                        source_state_dec,
                        e,
                    )
                    return

                if self.log_this_entity:
                    _LOGGER.debug("[%s] Timer - Calculated area: %s", self.entity_id, area)

                # Store state before update for logging
                state_before = self._state

                # Update the integral
                self._update_integral(area) # Logging is now inside _update_integral

                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Timer - State before update: %s, State after update: %s",
                        self.entity_id,
                        state_before,
                        self._state, # Log the state after update
                    )

                # Write state
                if self.log_this_entity:
                    _LOGGER.debug(
                        "[%s] Timer - Calling _async_write_ha_state(force_refresh=True) with state: %s",
                        self.entity_id,
                        self._state,
                    )
                self._attr_force_update = True  # Force update on state change
                self._async_write_ha_state()
                self._attr_force_update = False  # Force update on state change
                if self.log_this_entity:
                    _LOGGER.debug("[%s] Timer - Called _async_write_ha_state(force_refresh=True)", self.entity_id)


                # Update tracking variables
                self._last_integration_time = dt_util.utcnow() # Use utcnow for consistency
                self._last_integration_trigger = IntegrationTrigger.TIME_ELAPSED

                # Schedule the next integration
                if self.log_this_entity:
                    _LOGGER.debug("[%s] Rescheduling timer after execution", self.entity_id)
                self._schedule_max_sub_interval_exceeded_if_state_is_numeric(
                    source_state # Use the original source_state captured by closure
                )

            # Store the cancel handle correctly
            if self.log_this_entity:
                _LOGGER.debug(
                    "[%s] Scheduling timer with interval %s",
                    self.entity_id,
                    self._max_sub_interval,
                )
            self._cancel_max_sub_interval_exceeded_callback = async_call_later(
                self.hass,
                self._max_sub_interval,
                _integrate_on_max_sub_interval_exceeded_callback,
            )

    @property
    def native_value(self) -> Decimal | None:
        """Return the state of the sensor."""
        value = (
            round(self._state, self._round_digits)
            if isinstance(self._state, Decimal) and self._round_digits is not None
            else self._state
        )
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        return {
            "source_entity": self._source_entity_id,
        }

class SigenergyCalculatedSensors:
    """Class for holding calculated sensor methods."""

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
            icon="mdi:transmission-tower-import",
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
            icon="mdi:transmission-tower-export",
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
        # SigenergySensorEntityDescription(
        #     key="plant_accumulated_pv_energy",
        #     name="Accumulated PV Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL,
        #     source_key="plant_photovoltaic_power",  # Key of the source entity to use
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:solar-power",
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR
        # ),
        # SigenergySensorEntityDescription(
        #     key="plant_daily_pv_energy",
        #     name="Daily PV Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL_INCREASING,
        #     source_key="plant_photovoltaic_power",  # Key matches the PV power sensor
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:solar-power",
        # ),
        # SigenergySensorEntityDescription(
        #     key="plant_accumulated_grid_export_energy",
        #     name="Accumulated Grid Export Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL,
        #     source_key="plant_grid_export_power",  # Key matches the calculated sensor
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:transmission-tower-export",
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR
        # ),
        # SigenergySensorEntityDescription(
        #     key="plant_accumulated_grid_import_energy",
        #     name="Accumulated Grid Import Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL,
        #     source_key="plant_grid_import_power",  # Key matches the calculated sensor
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:transmission-tower-import",
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR
        # ),
        SigenergySensorEntityDescription(
            key="plant_daily_grid_export_energy",
            name="Daily Grid Export Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            state_class=SensorStateClass.TOTAL_INCREASING,
            source_key="plant_grid_export_power",  # Key matches the grid export power sensor
            round_digits=6,
            max_sub_interval=timedelta(seconds=30),
            icon="mdi:transmission-tower-export",
        ),
        SigenergySensorEntityDescription(
            key="plant_daily_grid_import_energy",
            name="Daily Grid Import Energy",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            suggested_display_precision=2,
            state_class=SensorStateClass.TOTAL_INCREASING,
            source_key="plant_grid_import_power",  # Key matches the grid import power sensor
            round_digits=6,
            max_sub_interval=timedelta(seconds=30),
            icon="mdi:transmission-tower-import",
        ),
        # SigenergySensorEntityDescription(
        #     key="plant_accumulated_consumed_energy",
        #     name="Accumulated Consumed Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL,
        #     source_key="plant_consumed_power",  # Key of the source entity to use
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:home-lightning-bolt",
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR
        # ),
        # SigenergySensorEntityDescription(
        #     key="plant_daily_consumed_energy",
        #     name="Daily Consumed Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL_INCREASING,
        #     source_key="plant_consumed_power",  # Key of the source entity to use
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:home-lightning-bolt",
        # ),
    ]

    # Add the inverter integration sensors list
    INVERTER_INTEGRATION_SENSORS = [
        # SigenergySensorEntityDescription(
        #     key="inverter_accumulated_pv_energy",
        #     name="Accumulated PV Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL,
        #     source_key="inverter_pv_power",  # Key matches the sensor in static_sensor.py
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:solar-power",
        #     suggested_unit_of_measurement=UnitOfEnergy.MEGA_WATT_HOUR
        # ),
        # SigenergySensorEntityDescription(
        #     key="inverter_daily_pv_energy",
        #     name="Daily PV Energy",
        #     device_class=SensorDeviceClass.ENERGY,
        #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        #     suggested_display_precision=2,
        #     state_class=SensorStateClass.TOTAL_INCREASING,
        #     source_key="inverter_pv_power",  # Key matches the sensor in static_sensor.py
        #     round_digits=6,
        #     max_sub_interval=timedelta(seconds=30),
        #     icon="mdi:solar-power",
        # ),
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

