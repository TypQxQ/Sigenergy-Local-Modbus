"""Constants for the Sigenergy ESS integration."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum

# Integration domain
DOMAIN = "sigen"
DEFAULT_NAME = "Sigenergy ESS"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SLAVE_ID = "slave_id"
CONF_PLANT_ID = "plant_id"
CONF_PLANT_CONNECTION = "plant_connection"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INVERTER_COUNT = "inverter_count"
CONF_INVERTER_SLAVE_ID = "inverter_slave_ids"
CONF_INVERTER_CONNECTIONS = "inverter_connections"
CONF_INVERTER_HAS_DCCHARGER = "inverter_has_dccharger"
CONF_AC_CHARGER_CONNECTIONS = "ac_charger_connections"
CONF_DC_CHARGER_CONNECTIONS = "dc_charger_connections"
CONF_DEVICE_TYPE = "device_type"
CONF_PARENT_DEVICE_ID = "parent_device_id"
CONF_VALUES_TO_INIT = "values_to_initialize"
STEP_ACCUMULATED_ENERGY_CONFIG = "accumulated_energy_config"

# Default names
DEFAULT_INVERTER_NAME = "Sigen Inverter"
DEFAULT_AC_CHARGER_NAME = "Sigen AC Charger"
DEFAULT_DC_CHARGER_NAME = "Sigen DC Charger"
DEFAULT_INVERTER_HAS_DCCHARGER = False

# Configuration step identifiers
STEP_USER = "user"
STEP_DEVICE_TYPE = "device_type"
STEP_PLANT_CONFIG = "plant_config"
STEP_DHCP_PLANT_CONFIG = "dhcp_plant_config"
STEP_INVERTER_CONFIG = "inverter_config"
STEP_AC_CHARGER_CONFIG = "ac_charger_config"
STEP_DC_CHARGER_CONFIG = "dc_charger_config"
STEP_SELECT_PLANT = "select_plant"
STEP_SELECT_INVERTER = "select_inverter"
STEP_DHCP_SELECT_PLANT = "dhcp_select_plant"
STEP_SELECT_DEVICE = "select_device"
STEP_RECONFIGURE = "reconfigure"

# Configuration constants
CONF_PARENT_PLANT_ID = "parent_plant_id"
CONF_PARENT_INVERTER_ID = "parent_inverter_id"
CONF_PLANT_ID = "plant_id"
CONF_READ_ONLY = "read_only"
CONF_SLAVE_ID = "slave_id"
CONF_RESET_VALUES = "reset_values"
CONF_REMOVE_DEVICE = "remove_device"

# Default values
DEFAULT_PORT = 502
DEFAULT_PLANT_SLAVE_ID = 247  # Plant address
DEFAULT_INVERTER_SLAVE_ID = 1  # Default Inverter address
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_INVERTER_COUNT = 1
DEFAULT_READ_ONLY = True  # Default to read-only mode
DEFAULT_MIN_INTEGRATION_TIME = 1  # Minimum integration time in seconds

# Platforms
PLATFORMS = ["sensor", "switch", "select", "number", "binary_sensor"]

# Device types
DEVICE_TYPE_NEW_PLANT = "new_plant"
DEVICE_TYPE_PLANT = "plant"
DEVICE_TYPE_INVERTER = "inverter"
DEVICE_TYPE_AC_CHARGER = "ac_charger"
DEVICE_TYPE_DC_CHARGER = "dc_charger"
DEVICE_TYPE_PV_STRING = "pv_string"
DEVICE_TYPE_UNKNOWN = "unknown"

# Modbus function codes
FUNCTION_READ_HOLDING_REGISTERS = 3
FUNCTION_READ_INPUT_REGISTERS = 4
FUNCTION_WRITE_REGISTER = 6
FUNCTION_WRITE_REGISTERS = 16

# Define a constant for the entity ID used to detect legacy YAML configuration
LEGACY_YAML_TEST_ENTITY_ID = "sensor.sigen_accumulated_energy_consumption"


# Map of new sensor keys to old legacy YAML entity IDs for migration
LEGACY_SENSOR_MIGRATION_MAP = {
    "sensor.sigen_plant_accumulated_pv_energy": "sensor.sigen_accumulated_pv_energy_production",
    "sensor.sigen_plant_accumulated_consumed_energy": "sensor.sigen_accumulated_energy_consumption",
    "sensor.sigen_plant_accumulated_grid_import_energy": "sensor.sigen_accumulated_grid_energy_import",
    "sensor.sigen_plant_accumulated_grid_export_energy": "sensor.sigen_accumulated_grid_energy_export",
}

RESETTABLE_SENSORS =  {
    "sensor.sigen_plant_accumulated_pv_energy": "Plant Accumulated PV Energy",
    "sensor.sigen_plant_accumulated_consumed_energy": "Plant Accumulated Consumed Energy",
    "sensor.sigen_plant_accumulated_grid_import_energy": "Plant Accumulated Grid Import Energy",
    "sensor.sigen_plant_accumulated_grid_export_energy": "Plant Accumulated Grid Export Energy",
}