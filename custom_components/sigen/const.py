"""Constants for the Sigenergy ESS integration."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Optional

# Import needed Home Assistant constants
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)

# Integration domain
DOMAIN = "sigen"
DEFAULT_NAME = "Sigenergy ESS"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SLAVE_ID = "slave_id"
CONF_PLANT_ID = "plant_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INVERTER_COUNT = "inverter_count"
CONF_AC_CHARGER_COUNT = "ac_charger_count"
CONF_DC_CHARGER_COUNT = "dc_charger_count"
CONF_INVERTER_SLAVE_ID = "inverter_slave_ids"
CONF_INVERTER_CONNECTIONS = "inverter_connections"
CONF_AC_CHARGER_SLAVE_ID = "ac_charger_slave_ids"
CONF_AC_CHARGER_CONNECTIONS = "ac_charger_connections"
CONF_DC_CHARGER_SLAVE_ID = "dc_charger_slave_ids"
CONF_DEVICE_TYPE = "device_type"
CONF_PARENT_DEVICE_ID = "parent_device_id"

# Default values
DEFAULT_PORT = 502
DEFAULT_SLAVE_ID = 247  # Plant address
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_INVERTER_COUNT = 1
DEFAULT_AC_CHARGER_COUNT = 0
DEFAULT_DC_CHARGER_COUNT = 0

# Energy calculation settings
DEFAULT_MAX_SUB_INTERVAL = 30  # Maximum time difference (in seconds) between energy readings

# Default names
DEFAULT_INVERTER_NAME = "Sigen Inverter"
DEFAULT_AC_CHARGER_NAME = "Sigen AC Charger"
DEFAULT_DC_CHARGER_NAME = "Sigen DC Charger"

# Configuration step identifiers
STEP_USER = "user"
STEP_DEVICE_TYPE = "device_type"
STEP_PLANT_CONFIG = "plant_config"
STEP_INVERTER_CONFIG = "inverter_config"
STEP_AC_CHARGER_CONFIG = "ac_charger_config"
STEP_DC_CHARGER_CONFIG = "dc_charger_config"
STEP_SELECT_PLANT = "select_plant"
STEP_SELECT_INVERTER = "select_inverter"

# Configuration constants
CONF_PARENT_PLANT_ID = "parent_plant_id"
CONF_PARENT_INVERTER_ID = "parent_inverter_id"
CONF_PLANT_ID = "plant_id"
CONF_READ_ONLY = "read_only"
CONF_SLAVE_ID = "slave_id"

# Default values
DEFAULT_PORT = 502
DEFAULT_PLANT_SLAVE_ID = 247  # Plant address
DEFAULT_INVERTER_SLAVE_ID = 1  # Default Inverter address
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_INVERTER_COUNT = 1
DEFAULT_AC_CHARGER_COUNT = 0
DEFAULT_DC_CHARGER_COUNT = 0
DEFAULT_READ_ONLY = True  # Default to read-only mode

# Platforms
PLATFORMS = ["sensor", "switch", "select", "number"]

# Device types
DEVICE_TYPE_NEW_PLANT = "new_plant"
DEVICE_TYPE_PLANT = "plant"
DEVICE_TYPE_INVERTER = "inverter"
DEVICE_TYPE_AC_CHARGER = "ac_charger"
DEVICE_TYPE_DC_CHARGER = "dc_charger"
DEVICE_TYPE_PV_STRING = "pv_string"

# Modbus function codes
FUNCTION_READ_HOLDING_REGISTERS = 3
FUNCTION_READ_INPUT_REGISTERS = 4
FUNCTION_WRITE_REGISTER = 6
FUNCTION_WRITE_REGISTERS = 16

# Modbus register types
class RegisterType(Enum):
    """Modbus register types."""

    READ_ONLY = "ro"
    HOLDING = "rw"
    WRITE_ONLY = "wo"

# Data types
class DataType(Enum):
    """Data types for Modbus registers."""

    U16 = "u16"
    U32 = "u32"
    U64 = "u64"
    S16 = "s16"
    S32 = "s32"
    STRING = "string"

# Running states (Appendix 1)
class RunningState(IntEnum):
    """Running states for Sigenergy devices."""

    STANDBY = 0
    RUNNING = 1
    FAULT = 2
    SHUTDOWN = 3

# EMS work modes
class EMSWorkMode(IntEnum):
    """EMS work modes."""

    MAX_SELF_CONSUMPTION = 0
    AI_MODE = 1
    TOU = 2
    REMOTE_EMS = 7

# Remote EMS control modes (Appendix 6)
class RemoteEMSControlMode(IntEnum):
    """Remote EMS control modes."""

    PCS_REMOTE_CONTROL = 0
    STANDBY = 1
    MAXIMUM_SELF_CONSUMPTION = 2
    COMMAND_CHARGING_GRID_FIRST = 3
    COMMAND_CHARGING_PV_FIRST = 4
    COMMAND_DISCHARGING_PV_FIRST = 5
    COMMAND_DISCHARGING_ESS_FIRST = 6

# Output types
class OutputType(IntEnum):
    """Output types for inverters."""

    L_N = 0
    L1_L2_L3 = 1
    L1_L2_L3_N = 2
    L1_L2_N = 3

# AC-Charger system states (Appendix 7)
class ACChargerSystemState(IntEnum):
    """System states for AC-Chargers."""

    SYSTEM_INIT = 0
    A1_A2 = 1
    B1 = 2
    B2 = 3
    C1 = 4
    C2 = 5
    F = 6
    E = 7

# Register definitions
@dataclass
class ModbusRegisterDefinition:
    """Modbus register definition."""

    address: int
    count: int
    register_type: RegisterType
    data_type: DataType
    gain: float
    unit: Optional[str] = None
    description: Optional[str] = None
    applicable_to: Optional[list[str]] = None
    is_supported: Optional[bool] = None  # Tracks whether register is supported by device

# Define register definitions based on PLANT_RUNNING_INFO_REGISTERS.csv
PLANT_RUNNING_INFO_REGISTERS = {
    "plant_system_time": ModbusRegisterDefinition(
        address=30000,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1,
        unit="s",
        description="System time (Epoch seconds)",
    ),
    "plant_system_timezone": ModbusRegisterDefinition(
        address=30002,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=1,
        unit="min",
        description="System timezone",
    ),
    "plant_ems_work_mode": ModbusRegisterDefinition(
        address=30003,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="EMS work mode",
    ),
    "plant_grid_sensor_status": ModbusRegisterDefinition(
        address=30004,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Grid Sensor Status (0: not connected, 1: connected)",
    ),
    "plant_grid_sensor_active_power": ModbusRegisterDefinition(
        address=30005,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Grid Active Power (>0 buy from grid; <0 sell to grid)",
    ),
    "plant_grid_sensor_reactive_power": ModbusRegisterDefinition(
        address=30007,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Grid Reactive Power",
    ),
    "plant_on_off_grid_status": ModbusRegisterDefinition(
        address=30009,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="On/Off Grid status (0: ongrid, 1: offgrid(auto), 2: offgrid(manual))",
    ),
    "plant_max_active_power": ModbusRegisterDefinition(
        address=30010,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Max active power",
    ),
    "plant_max_apparent_power": ModbusRegisterDefinition(
        address=30012,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit="kVar",
        description="Max apparent power",
    ),
    "plant_ess_soc": ModbusRegisterDefinition(
        address=30014,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="Battery State of Charge",
    ),
    "plant_phase_a_active_power": ModbusRegisterDefinition(
        address=30015,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Plant phase A active power",
    ),
    "plant_phase_b_active_power": ModbusRegisterDefinition(
        address=30017,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Plant phase B active power",
    ),
    "plant_phase_c_active_power": ModbusRegisterDefinition(
        address=30019,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Plant phase C active power",
    ),
    "plant_phase_a_reactive_power": ModbusRegisterDefinition(
        address=30021,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Plant phase A reactive power",
    ),
    "plant_phase_b_reactive_power": ModbusRegisterDefinition(
        address=30023,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Plant phase B reactive power",
    ),
    "plant_phase_c_reactive_power": ModbusRegisterDefinition(
        address=30025,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Plant phase C reactive power",
    ),
    "plant_general_alarm1": ModbusRegisterDefinition(
        address=30027,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="General Alarm1 (Refer to Appendix2)",
    ),
    "plant_general_alarm2": ModbusRegisterDefinition(
        address=30028,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="General Alarm2 (Refer to Appendix3)",
    ),
    "plant_general_alarm3": ModbusRegisterDefinition(
        address=30029,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="General Alarm3 (Refer to Appendix4)",
    ),
    "plant_general_alarm4": ModbusRegisterDefinition(
        address=30030,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="General Alarm4 (Refer to Appendix5)",
    ),
    "plant_active_power": ModbusRegisterDefinition(
        address=30031,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Plant active power",
    ),
    "plant_reactive_power": ModbusRegisterDefinition(
        address=30033,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Plant reactive power",
    ),
    "plant_photovoltaic_power": ModbusRegisterDefinition(
        address=30035,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Photovoltaic power",
    ),
    "plant_ess_power": ModbusRegisterDefinition(
        address=30037,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS power (<0: discharging, >0: charging)",
    ),
    "plant_available_max_active_power": ModbusRegisterDefinition(
        address=30039,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Available max active power",
    ),
    "plant_available_min_active_power": ModbusRegisterDefinition(
        address=30041,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Available min active power",
    ),
    "plant_available_max_reactive_power": ModbusRegisterDefinition(
        address=30043,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit="kVar",
        description="Available max reactive power",
    ),
    "plant_available_min_reactive_power": ModbusRegisterDefinition(
        address=30045,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit="kVar",
        description="Available min reactive power",
    ),
    "plant_ess_available_max_charging_power": ModbusRegisterDefinition(
        address=30047,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Available max charging power",
    ),
    "plant_ess_available_max_discharging_power": ModbusRegisterDefinition(
        address=30049,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Available max discharging power",
    ),
    "plant_running_state": ModbusRegisterDefinition(
        address=30051,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Plant running state (Refer to Appendix1)",
    ),
    "plant_grid_sensor_phase_a_active_power": ModbusRegisterDefinition(
        address=30052,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Grid sensor Phase A active power",
    ),
    "plant_grid_sensor_phase_b_active_power": ModbusRegisterDefinition(
        address=30054,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Grid sensor Phase B active power",
    ),
    "plant_grid_sensor_phase_c_active_power": ModbusRegisterDefinition(
        address=30056,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Grid sensor Phase C active power",
    ),
    "plant_grid_sensor_phase_a_reactive_power": ModbusRegisterDefinition(
        address=30058,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Grid sensor Phase A reactive power",
    ),
    "plant_grid_sensor_phase_b_reactive_power": ModbusRegisterDefinition(
        address=30060,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Grid sensor Phase B reactive power",
    ),
    "plant_grid_sensor_phase_c_reactive_power": ModbusRegisterDefinition(
        address=30062,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Grid sensor Phase C reactive power",
    ),
    "plant_ess_available_max_charging_capacity": ModbusRegisterDefinition(
        address=30064,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Available max charging capacity",
    ),
    "plant_ess_available_max_discharging_capacity": ModbusRegisterDefinition(
        address=30066,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Available max discharging capacity",
    ),
    "plant_ess_rated_charging_power": ModbusRegisterDefinition(
        address=30068,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Rated charging power",
    ),
    "plant_ess_rated_discharging_power": ModbusRegisterDefinition(
        address=30070,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Rated discharging power",
    ),
    "plant_general_alarm5": ModbusRegisterDefinition(
        address=30072,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="General Alarm5 (Refer to Appendix11)",
    ),
    "plant_ess_rated_energy_capacity": ModbusRegisterDefinition(
        address=30083,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS rated energy capacity",
    ),
    "plant_ess_charge_cut_off_soc": ModbusRegisterDefinition(
        address=30085,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="ESS charge Cut-Off SOC",
    ),
    "plant_ess_discharge_cut_off_soc": ModbusRegisterDefinition(
        address=30086,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="ESS discharge Cut-Off SOC",
    ),
    "plant_ess_soh": ModbusRegisterDefinition(
        address=30087,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="Battery State of Health (weighted average of all ESS devices)",
    ),
}

PLANT_PARAMETER_REGISTERS = {
    "plant_start_stop": ModbusRegisterDefinition(
        address=40000,
        count=1,
        register_type=RegisterType.WRITE_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Start/Stop (0: Stop 1: Start)",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_active_power_fixed_target": ModbusRegisterDefinition(
        address=40001,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Active power fixed adjustment target value",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_reactive_power_fixed_target": ModbusRegisterDefinition(
        address=40003,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Reactive power fixed adjustment target value. Range: [-60.00 * base value, 60.00 * base value]. Takes effect globally regardless of the EMS operating mode.",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_active_power_percentage_target": ModbusRegisterDefinition(
        address=40005,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Active power percentage adjustment target value. Range: [-100.00, 100.00]",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_qs_ratio_target": ModbusRegisterDefinition(
        address=40006,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Q/S adjustment target value",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_power_factor_target": ModbusRegisterDefinition(
        address=40007,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=1000,
        description="Power factor adjustment target value",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_phase_a_active_power_fixed_target": ModbusRegisterDefinition(
        address=40008,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Phase A active power fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_b_active_power_fixed_target": ModbusRegisterDefinition(
        address=40010,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Phase B active power fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_c_active_power_fixed_target": ModbusRegisterDefinition(
        address=40012,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Phase C active power fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_a_reactive_power_fixed_target": ModbusRegisterDefinition(
        address=40014,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Phase A reactive power fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_b_reactive_power_fixed_target": ModbusRegisterDefinition(
        address=40016,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Phase B reactive power fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_c_reactive_power_fixed_target": ModbusRegisterDefinition(
        address=40018,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Phase C reactive power fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_a_active_power_percentage_target": ModbusRegisterDefinition(
        address=40020,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Phase A Active power percentage adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_b_active_power_percentage_target": ModbusRegisterDefinition(
        address=40021,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Phase B Active power percentage adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_c_active_power_percentage_target": ModbusRegisterDefinition(
        address=40022,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Phase C Active power percentage adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_a_qs_ratio_target": ModbusRegisterDefinition(
        address=40023,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Phase A Q/S fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_b_qs_ratio_target": ModbusRegisterDefinition(
        address=40024,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Phase B Q/S fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_phase_c_qs_ratio_target": ModbusRegisterDefinition(
        address=40025,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Phase C Q/S fixed adjustment target value",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_remote_ems_enable": ModbusRegisterDefinition(
        address=40029,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U16,
        gain=1,
        description="Remote EMS enable (0: disabled 1: enabled). When enabled, the plant's EMS work mode (30003) will switch to remote EMS.",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_independent_phase_power_control_enable": ModbusRegisterDefinition(
        address=40030,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U16,
        gain=1,
        description="Independent phase power control enable (0: disabled 1: enabled)",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_remote_ems_control_mode": ModbusRegisterDefinition(
        address=40031,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U16,
        gain=1,
        description="Remote EMS control mode",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_ess_max_charging_limit": ModbusRegisterDefinition(
        address=40032,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS max charging limit",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_ess_max_discharging_limit": ModbusRegisterDefinition(
        address=40034,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS max discharging limit",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_pv_max_power_limit": ModbusRegisterDefinition(
        address=40036,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="PV max power limit",
        applicable_to=["hybrid_inverter"],
    ),
    "plant_grid_point_maximum_export_limitation": ModbusRegisterDefinition(
        address=40038,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Grid Point Maximum export limitation",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_grid_maximum_import_limitation": ModbusRegisterDefinition(
        address=40040,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Grid Point Maximum import limitation",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_pcs_maximum_export_limitation": ModbusRegisterDefinition(
        address=40042,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="PCS maximum export limitation. Range: [0, 0xFFFFFFFE]. With value 0xFFFFFFFF, register is not valid. Takes effect globally.",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "plant_pcs_maximum_import_limitation": ModbusRegisterDefinition(
        address=40044,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="PCS maximum import limitation. Range: [0, 0xFFFFFFFE]. With value 0xFFFFFFFF, register is not valid. Takes effect globally.",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
}

INVERTER_RUNNING_INFO_REGISTERS = {
    "inverter_model_type": ModbusRegisterDefinition(
        address=30500,
        count=15,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.STRING,
        gain=1,
        description="Model Type",
    ),
    "inverter_serial_number": ModbusRegisterDefinition(
        address=30515,
        count=10,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.STRING,
        gain=1,
        description="Serial Number",
    ),
    "inverter_machine_firmware_version": ModbusRegisterDefinition(
        address=30525,
        count=15,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.STRING,
        gain=1,
        description="Firmware Version",
    ),
    "inverter_rated_active_power": ModbusRegisterDefinition(
        address=30540,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Rated Active Power",
    ),
    "inverter_max_apparent_power": ModbusRegisterDefinition(
        address=30542,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit="kVA",
        description="Max. Apparent Power",
    ),
    "inverter_max_active_power": ModbusRegisterDefinition(
        address=30544,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Max. Active Power",
    ),
    "inverter_max_absorption_power": ModbusRegisterDefinition(
        address=30546,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Max. Absorption Power",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_rated_battery_capacity": ModbusRegisterDefinition(
        address=30548,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="Rated Battery Capacity",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_rated_charge_power": ModbusRegisterDefinition(
        address=30550,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Rated Charge Power",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_rated_discharge_power": ModbusRegisterDefinition(
        address=30552,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Rated Discharge Power",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_daily_charge_energy": ModbusRegisterDefinition(
        address=30566,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Daily Charge Energy",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_accumulated_charge_energy": ModbusRegisterDefinition(
        address=30568,
        count=4,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U64,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Accumulated Charge Energy",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_daily_discharge_energy": ModbusRegisterDefinition(
        address=30572,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Daily Discharge Energy",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_accumulated_discharge_energy": ModbusRegisterDefinition(
        address=30574,
        count=4,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U64,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Accumulated Discharge Energy",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_running_state": ModbusRegisterDefinition(
        address=30578,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Running State (Refer to Appendix 1)",
    ),
    "inverter_max_active_power_adjustment_value": ModbusRegisterDefinition(
        address=30579,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Max. Active Power Adjustment Value",
    ),
    "inverter_min_active_power_adjustment_value": ModbusRegisterDefinition(
        address=30581,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Min. Active Power Adjustment Value",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_max_reactive_power_adjustment_value_fed": ModbusRegisterDefinition(
        address=30583,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit="kVar",
        description="Max. Reactive Power Adjustment Value Fed to AC Terminal",
    ),
    "inverter_max_reactive_power_adjustment_value_absorbed": ModbusRegisterDefinition(
        address=30585,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit="kVar",
        description="Max. Reactive Power Adjustment Value Absorbed from AC Terminal",
    ),
    "inverter_active_power": ModbusRegisterDefinition(
        address=30587,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Active Power",
    ),
    "inverter_reactive_power": ModbusRegisterDefinition(
        address=30589,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Reactive Power",
    ),
    "inverter_ess_max_battery_charge_power": ModbusRegisterDefinition(
        address=30591,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Max. Battery Charge Power",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_max_battery_discharge_power": ModbusRegisterDefinition(
        address=30593,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Max. Battery Discharge Power",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_available_battery_charge_energy": ModbusRegisterDefinition(
        address=30595,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Available Battery Charge Energy",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_available_battery_discharge_energy": ModbusRegisterDefinition(
        address=30597,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="ESS Available Battery Discharge Energy",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_charge_discharge_power": ModbusRegisterDefinition(
        address=30599,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="ESS Charge/Discharge Power",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_battery_soc": ModbusRegisterDefinition(
        address=30601,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="Battery State of Charge",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_battery_soh": ModbusRegisterDefinition(
        address=30602,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="Battery State of Health",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_average_cell_temperature": ModbusRegisterDefinition(
        address=30603,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfTemperature.CELSIUS,
        description="Battery Average Cell Temperature",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_average_cell_voltage": ModbusRegisterDefinition(
        address=30604,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1000,
        unit=UnitOfElectricPotential.VOLT,
        description="Battery Average Cell Voltage",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_alarm1": ModbusRegisterDefinition(
        address=30605,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm1 (Refer to Appendix 2)",
    ),
    "inverter_alarm2": ModbusRegisterDefinition(
        address=30606,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm2 (Refer to Appendix 3)",
    ),
    "inverter_alarm3": ModbusRegisterDefinition(
        address=30607,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm3 (Refer to Appendix 4)",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_alarm4": ModbusRegisterDefinition(
        address=30608,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm4 (Refer to Appendix 5)",
    ),
    "inverter_alarm5": ModbusRegisterDefinition(
        address=30609,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm5 (Refer to Appendix 11)",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_maximum_battery_temperature": ModbusRegisterDefinition(
        address=30620,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfTemperature.CELSIUS,
        description="Battery Maximum Temperature",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_minimum_battery_temperature": ModbusRegisterDefinition(
        address=30621,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfTemperature.CELSIUS,
        description="Battery Minimum Temperature",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_maximum_battery_cell_voltage": ModbusRegisterDefinition(
        address=30622,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1000,
        unit=UnitOfElectricPotential.VOLT,
        description="Battery Maximum Cell Voltage",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_ess_minimum_battery_cell_voltage": ModbusRegisterDefinition(
        address=30623,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1000,
        unit=UnitOfElectricPotential.VOLT,
        description="Battery Minimum Cell Voltage",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_rated_grid_voltage": ModbusRegisterDefinition(
        address=31000,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="Rated Grid Voltage",
    ),
    "inverter_rated_grid_frequency": ModbusRegisterDefinition(
        address=31001,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=100,
        unit=UnitOfFrequency.HERTZ,
        description="Rated Grid Frequency",
    ),
    "inverter_grid_frequency": ModbusRegisterDefinition(
        address=31002,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=100,
        unit=UnitOfFrequency.HERTZ,
        description="Grid Frequency",
    ),
    "inverter_pcs_internal_temperature": ModbusRegisterDefinition(
        address=31003,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfTemperature.CELSIUS,
        description="PCS Internal Temperature",
    ),
    "inverter_output_type": ModbusRegisterDefinition(
        address=31004,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Output Type (0: L/N, 1: L1/L2/L3, 2: L1/L2/L3/N, 3: L1/L2/N)",
    ),
    "inverter_ab_line_voltage": ModbusRegisterDefinition(
        address=31005,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricPotential.VOLT,
        description="A-B Line Voltage",
    ),
    "inverter_bc_line_voltage": ModbusRegisterDefinition(
        address=31007,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricPotential.VOLT,
        description="B-C Line Voltage",
    ),
    "inverter_ca_line_voltage": ModbusRegisterDefinition(
        address=31009,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricPotential.VOLT,
        description="C-A Line Voltage",
    ),
    "inverter_phase_a_voltage": ModbusRegisterDefinition(
        address=31011,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricPotential.VOLT,
        description="Phase A Voltage",
    ),
    "inverter_phase_b_voltage": ModbusRegisterDefinition(
        address=31013,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricPotential.VOLT,
        description="Phase B Voltage",
    ),
    "inverter_phase_c_voltage": ModbusRegisterDefinition(
        address=31015,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricPotential.VOLT,
        description="Phase C Voltage",
    ),
    "inverter_phase_a_current": ModbusRegisterDefinition(
        address=31017,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="Phase A Current",
    ),
    "inverter_phase_b_current": ModbusRegisterDefinition(
        address=31019,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="Phase B Current",
    ),
    "inverter_phase_c_current": ModbusRegisterDefinition(
        address=31021,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="Phase C Current",
    ),
    "inverter_power_factor": ModbusRegisterDefinition(
        address=31023,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1000,
        description="Power Factor",
    ),
    "inverter_pack_count": ModbusRegisterDefinition(
        address=31024,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="PACK Count",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_pv_string_count": ModbusRegisterDefinition(
        address=31025,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="PV String Count",
    ),
    "inverter_mppt_count": ModbusRegisterDefinition(
        address=31026,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="MPPT Count",
    ),
    "inverter_pv1_voltage": ModbusRegisterDefinition(
        address=31027,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV1 Voltage",
    ),
    "inverter_pv1_current": ModbusRegisterDefinition(
        address=31028,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV1 Current",
    ),
    "inverter_pv2_voltage": ModbusRegisterDefinition(
        address=31029,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV2 Voltage",
    ),
    "inverter_pv2_current": ModbusRegisterDefinition(
        address=31030,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV2 Current",
    ),
    "inverter_pv3_voltage": ModbusRegisterDefinition(
        address=31031,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV3 Voltage",
    ),
    "inverter_pv3_current": ModbusRegisterDefinition(
        address=31032,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV3 Current",
    ),
    "inverter_pv4_voltage": ModbusRegisterDefinition(
        address=31033,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV4 Voltage",
    ),
    "inverter_pv4_current": ModbusRegisterDefinition(
        address=31034,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV4 Current",
    ),
    "inverter_pv_power": ModbusRegisterDefinition(
        address=31035,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="PV Power",
    ),
    "inverter_insulation_resistance": ModbusRegisterDefinition(
        address=31037,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1000,
        unit="MΩ",
        description="Insulation Resistance",
    ),
    "inverter_startup_time": ModbusRegisterDefinition(
        address=31038,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1,
        unit="s",
        description="Startup Time",
    ),
    "inverter_shutdown_time": ModbusRegisterDefinition(
        address=31040,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1,
        unit="s",
        description="Shutdown Time",
    ),
    "inverter_pv5_voltage": ModbusRegisterDefinition(
        address=31042,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV5 Voltage",
    ),
    "inverter_pv5_current": ModbusRegisterDefinition(
        address=31043,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV5 Current",
    ),
    "inverter_pv6_voltage": ModbusRegisterDefinition(
        address=31044,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV6 Voltage",
    ),
    "inverter_pv6_current": ModbusRegisterDefinition(
        address=31045,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV6 Current",
    ),
    "inverter_pv7_voltage": ModbusRegisterDefinition(
        address=31046,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV7 Voltage",
    ),
    "inverter_pv7_current": ModbusRegisterDefinition(
        address=31047,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV7 Current",
    ),
    "inverter_pv8_voltage": ModbusRegisterDefinition(
        address=31048,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV8 Voltage",
    ),
    "inverter_pv8_current": ModbusRegisterDefinition(
        address=31049,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV8 Current",
    ),
    "inverter_pv9_voltage": ModbusRegisterDefinition(
        address=31050,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV9 Voltage",
    ),
    "inverter_pv9_current": ModbusRegisterDefinition(
        address=31051,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV9 Current",
    ),
    "inverter_pv10_voltage": ModbusRegisterDefinition(
        address=31052,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV10 Voltage",
    ),
    "inverter_pv10_current": ModbusRegisterDefinition(
        address=31053,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV10 Current",
    ),
    "inverter_pv11_voltage": ModbusRegisterDefinition(
        address=31054,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV11 Voltage",
    ),
    "inverter_pv11_current": ModbusRegisterDefinition(
        address=31055,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV11 Current",
    ),
    "inverter_pv12_voltage": ModbusRegisterDefinition(
        address=31056,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV12 Voltage",
    ),
    "inverter_pv12_current": ModbusRegisterDefinition(
        address=31057,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV12 Current",
    ),
    "inverter_pv13_voltage": ModbusRegisterDefinition(
        address=31058,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV13 Voltage",
    ),
    "inverter_pv13_current": ModbusRegisterDefinition(
        address=31059,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV13 Current",
    ),
    "inverter_pv14_voltage": ModbusRegisterDefinition(
        address=31060,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV14 Voltage",
    ),
    "inverter_pv14_current": ModbusRegisterDefinition(
        address=31061,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV14 Current",
    ),
    "inverter_pv15_voltage": ModbusRegisterDefinition(
        address=31062,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV15 Voltage",
    ),
    "inverter_pv15_current": ModbusRegisterDefinition(
        address=31063,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV15 Current",
    ),
    "inverter_pv16_voltage": ModbusRegisterDefinition(
        address=31064,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="PV16 Voltage",
    ),
    "inverter_pv16_current": ModbusRegisterDefinition(
        address=31065,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S16,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="PV16 Current",
    ),
}

INVERTER_PARAMETER_REGISTERS = {
    "inverter_start_stop": ModbusRegisterDefinition(
        address=40500,
        count=1,
        register_type=RegisterType.WRITE_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Start/Stop inverter (0: Stop 1: Start)",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "inverter_grid_code": ModbusRegisterDefinition(
        address=40501,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U16,
        gain=1,
        description="Grid code setting",
        applicable_to=["hybrid_inverter", "pv_inverter"],
    ),
    "inverter_remote_ems_dispatch_enable": ModbusRegisterDefinition(
        address=41500,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U16,
        gain=1,
        description="Remote EMS dispatch enable (0: disabled 1: enabled)",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_active_power_fixed_adjustment": ModbusRegisterDefinition(
        address=41501,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Active power fixed value adjustment",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_reactive_power_fixed_adjustment": ModbusRegisterDefinition(
        address=41503,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S32,
        gain=1000,
        unit="kVar",
        description="Reactive power fixed value adjustment",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_active_power_percentage_adjustment": ModbusRegisterDefinition(
        address=41505,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Active power percentage adjustment",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_reactive_power_qs_adjustment": ModbusRegisterDefinition(
        address=41506,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=100,
        unit=PERCENTAGE,
        description="Reactive power Q/S adjustment",
        applicable_to=["hybrid_inverter"],
    ),
    "inverter_power_factor_adjustment": ModbusRegisterDefinition(
        address=41507,
        count=1,
        register_type=RegisterType.HOLDING,
        data_type=DataType.S16,
        gain=1000,
        description="Power factor adjustment",
        applicable_to=["hybrid_inverter"],
    ),
}

AC_CHARGER_RUNNING_INFO_REGISTERS = {
    "ac_charger_system_state": ModbusRegisterDefinition(
        address=32000,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="System states according to IEC61851-1 definition",
    ),
    "ac_charger_total_energy_consumed": ModbusRegisterDefinition(
        address=32001,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="Total energy consumed",
    ),
    "ac_charger_charging_power": ModbusRegisterDefinition(
        address=32003,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Charging power",
    ),
    "ac_charger_rated_power": ModbusRegisterDefinition(
        address=32005,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="Rated power",
    ),
    "ac_charger_rated_current": ModbusRegisterDefinition(
        address=32007,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="Rated current",
    ),
    "ac_charger_rated_voltage": ModbusRegisterDefinition(
        address=32009,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="Rated voltage",
    ),
    "ac_charger_input_breaker_rated_current": ModbusRegisterDefinition(
        address=32010,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="AC-Charger input breaker rated current",
    ),
    "ac_charger_alarm1": ModbusRegisterDefinition(
        address=32012,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm1",
    ),
    "ac_charger_alarm2": ModbusRegisterDefinition(
        address=32013,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm2",
    ),
    "ac_charger_alarm3": ModbusRegisterDefinition(
        address=32014,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Alarm3",
    ),
}

AC_CHARGER_PARAMETER_REGISTERS = {
    "ac_charger_start_stop": ModbusRegisterDefinition(
        address=42000,
        count=1,
        register_type=RegisterType.WRITE_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="Start/Stop AC Charger (0: Start 1: Stop)",
    ),
    "ac_charger_output_current": ModbusRegisterDefinition(
        address=42001,
        count=2,
        register_type=RegisterType.HOLDING,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfElectricCurrent.AMPERE,
        description="Charger output current ([6, X] X is the smaller value between the rated current and the AC-Charger input breaker rated current.)",
    ),
}

# DC Charger register definitions
DC_CHARGER_RUNNING_INFO_REGISTERS = {
    "dc_charger_vehicle_battery_voltage": ModbusRegisterDefinition(
        address=31500,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=UnitOfElectricPotential.VOLT,
        description="DC Charger Vehicle Battery Voltage",
        applicable_to=["hybrid_inverter"],
    ),
    "dc_charger_charging_current": ModbusRegisterDefinition(
        address=31501,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=UnitOfElectricCurrent.AMPERE,
        description="DC Charger Charging Current",
        applicable_to=["hybrid_inverter"],
    ),
    "dc_charger_output_power": ModbusRegisterDefinition(
        address=31502,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.S32,
        gain=1000,
        unit=UnitOfPower.KILO_WATT,
        description="DC Charger Output Power",
        applicable_to=["hybrid_inverter"],
    ),
    "dc_charger_vehicle_soc": ModbusRegisterDefinition(
        address=31504,
        count=1,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U16,
        gain=10,
        unit=PERCENTAGE,
        description="DC Charger Vehicle SOC",
        applicable_to=["hybrid_inverter"],
    ),
    "dc_charger_current_charging_capacity": ModbusRegisterDefinition(
        address=31505,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=100,
        unit=UnitOfEnergy.KILO_WATT_HOUR,
        description="DC Charger Current Charging Capacity (Single Time)",
        applicable_to=["hybrid_inverter"],
    ),
    "dc_charger_current_charging_duration": ModbusRegisterDefinition(
        address=31507,
        count=2,
        register_type=RegisterType.READ_ONLY,
        data_type=DataType.U32,
        gain=1,
        unit="s",
        description="DC Charger Current Charging Duration (Single Time)",
        applicable_to=["hybrid_inverter"],
    ),
}

DC_CHARGER_PARAMETER_REGISTERS = {
    "dc_charger_start_stop": ModbusRegisterDefinition(
        address=41000,
        count=1,
        register_type=RegisterType.WRITE_ONLY,
        data_type=DataType.U16,
        gain=1,
        description="DC Charger Start/Stop (0: Start 1: Stop)",
        applicable_to=["hybrid_inverter"],
    ),
}
