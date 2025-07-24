"""Modbus communication for Sigenergy ESS."""
from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry  # pylint: disable=no-name-in-module, syntax-error
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.exceptions import ConnectionException, ModbusException
# from pymodbus.payload import BinaryPayloadBuilder
# from pymodbus.client.mixin import ModbusClientMixin

from .const import (
    CONF_INVERTER_CONNECTIONS,
    CONF_AC_CHARGER_CONNECTIONS,
    CONF_PLANT_ID,
    CONF_SLAVE_ID,
    CONF_HOST,
    CONF_PORT,
    CONF_READ_ONLY,
    CONF_PLANT_CONNECTION,
    DEFAULT_PLANT_SLAVE_ID,
    DEFAULT_READ_ONLY,
)
from .modbusregisterdefinitions import (
    DataType,
    RegisterType,
    UpdateFrequencyType,
    ModbusRegisterDefinition,
    PLANT_RUNNING_INFO_REGISTERS,
    PLANT_PARAMETER_REGISTERS,
    INVERTER_RUNNING_INFO_REGISTERS,
    INVERTER_PARAMETER_REGISTERS,
    AC_CHARGER_RUNNING_INFO_REGISTERS,
    AC_CHARGER_PARAMETER_REGISTERS,
    DC_CHARGER_RUNNING_INFO_REGISTERS,
    DC_CHARGER_PARAMETER_REGISTERS,
)


# Compatibility class for ModbusClientMixin
class ModbusClientMixin:
    class DATATYPE:
        UINT16 = "uint16"
        INT16 = "int16"
        UINT32 = "uint32"
        INT32 = "int32"
        UINT64 = "uint64"
        STRING = "string"
    
    @staticmethod
    def convert_from_registers(registers, data_type):
        if not registers:
            return 0
        if data_type == ModbusClientMixin.DATATYPE.UINT16:
            return registers[0] & 0xFFFF
        elif data_type == ModbusClientMixin.DATATYPE.INT16:
            val = registers[0] & 0xFFFF
            return val if val < 32768 else val - 65536
        elif data_type == ModbusClientMixin.DATATYPE.UINT32:
            if len(registers) >= 2:
                return (registers[0] << 16) | registers[1]
            return registers[0]
        elif data_type == ModbusClientMixin.DATATYPE.INT32:
            if len(registers) >= 2:
                val = (registers[0] << 16) | registers[1]
                return val if val < 2147483648 else val - 4294967296
            return registers[0]
        elif data_type == ModbusClientMixin.DATATYPE.UINT64:
            if len(registers) >= 4:
                return (registers[0] << 48) | (registers[1] << 32) | (registers[2] << 16) | registers[3]
            return registers[0]
        elif data_type == ModbusClientMixin.DATATYPE.STRING:
            result = []
            for reg in registers:
                high_byte = (reg >> 8) & 0xFF
                low_byte = reg & 0xFF
                if high_byte > 0:
                    result.append(chr(high_byte))
                if low_byte > 0:
                    result.append(chr(low_byte))
            return "".join(result).rstrip("\x00")
        else:
            return registers[0] if registers else 0

_LOGGER = logging.getLogger(__name__)

@dataclass
class ModbusConnectionConfig:
    """Configuration for a Modbus connection."""
    name: str
    host: str
    port: int
    slave_id: int


@contextmanager
def _suppress_pymodbus_logging(really_suppress: bool = True):
    """Temporarily suppress pymodbus logging."""
    if really_suppress:
        pymodbus_logger = logging.getLogger("pymodbus")
        original_level = pymodbus_logger.level
        original_propagate = pymodbus_logger.propagate
        pymodbus_logger.setLevel(logging.CRITICAL)
        pymodbus_logger.propagate = False
    try:
        yield
    finally:
        if really_suppress:
            pymodbus_logger.setLevel(original_level)
            pymodbus_logger.propagate = original_propagate


# Simple BinaryPayloadBuilder replacement
class BinaryPayloadBuilder:
    def __init__(self, byteorder=None, wordorder=None, repack=False):
        self._payload = []
        self._byteorder = byteorder or "big"
        self._wordorder = wordorder or "big"
    
    def add_16bit_uint(self, value):
        self._payload.append(int(value) & 0xFFFF)
    
    def add_16bit_int(self, value):
        val = int(value)
        if val < 0:
            val = 0x10000 + val
        self._payload.append(val & 0xFFFF)
    
    def add_32bit_uint(self, value):
        val = int(value)
        self._payload.append((val >> 16) & 0xFFFF)
        self._payload.append(val & 0xFFFF)
    
    def add_32bit_int(self, value):
        val = int(value)
        if val < 0:
            val = 0x100000000 + val
        self._payload.append((val >> 16) & 0xFFFF)
        self._payload.append(val & 0xFFFF)
    
    def add_64bit_uint(self, value):
        val = int(value)
        self._payload.append((val >> 48) & 0xFFFF)
        self._payload.append((val >> 32) & 0xFFFF)
        self._payload.append((val >> 16) & 0xFFFF)
        self._payload.append(val & 0xFFFF)
    
    def add_string(self, value):
        string_bytes = str(value).encode("utf-8")
        for i in range(0, len(string_bytes), 2):
            if i + 1 < len(string_bytes):
                word = (string_bytes[i] << 8) | string_bytes[i + 1]
            else:
                word = string_bytes[i] << 8
            self._payload.append(word)
    
    def to_registers(self):
        return self._payload

class SigenergyModbusError(HomeAssistantError):
    """Exception for Sigenergy Modbus errors."""


class SigenergyModbusHub:
    """Modbus hub for Sigenergy ESS."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the Modbus hub."""
        self.hass = hass
        self.config_entry = config_entry

        # Dictionary to store Modbus clients for different connections
        # Key is (host, port) tuple, value is the client instance
        self._clients: Dict[Tuple[str, int], AsyncModbusTcpClient] = {}
        self._locks: Dict[Tuple[str, int], asyncio.Lock] = {}
        self._connected: Dict[Tuple[str, int], bool] = {}

        # Store connection for plant
        self.plant_connection = config_entry.data.get(CONF_PLANT_CONNECTION, {})
        self._plant_host = self.plant_connection[CONF_HOST]
        self._plant_port = self.plant_connection[CONF_PORT]
        self.plant_id = self.plant_connection.get(CONF_PLANT_ID, DEFAULT_PLANT_SLAVE_ID)

        # Read-only mode setting
        self.read_only = self.plant_connection.get(CONF_READ_ONLY, DEFAULT_READ_ONLY)

        # Get inverter connections
        self.inverter_connections = config_entry.data.get(CONF_INVERTER_CONNECTIONS, {})
        _LOGGER.debug("Inverter connections: %s", self.inverter_connections)
        self.inverter_count = len(self.inverter_connections)

        # Get AC Charger connections
        self.ac_charger_connections = config_entry.data.get(CONF_AC_CHARGER_CONNECTIONS, {})
        _LOGGER.debug("AC Charger connections: %s", self.ac_charger_connections)
        self.ac_charger_count = len(self.ac_charger_connections)

        # Other slave IDs and their connection details

        # Initialize register support status
        self.plant_registers_probed = False
        self.inverter_registers_probed = set()
        self.ac_charger_registers_probed = set()
        self.dc_charger_registers_probed = set()

    def _get_connection_key(self, device_info: dict) -> Tuple[str, int]:
        """Get the connection key (host, port) for a device_info dict."""
        return (device_info[CONF_HOST], device_info[CONF_PORT])

    async def _get_client(self, device_info: dict) -> AsyncModbusTcpClient:
        """Get or create a Modbus client for the given device_info dict."""

        key = self._get_connection_key(device_info)

        if key not in self._clients or not self._connected.get(key, False):
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()

            async with self._locks[key]:
                if key not in self._clients or not self._connected.get(key, False):
                    host, port = key
                    _LOGGER.debug("Attempting to create new Modbus client for %s:%s", host, port)
                    self._clients[key] = AsyncModbusTcpClient(
                        host=host,
                        port=port,
                        timeout=20, # Increased timeout to 20 seconds
                        retries=3
                    )

                    _LOGGER.debug("Attempting to connect client for %s:%s", host, port)
                    connected = await self._clients[key].connect()
                    if not connected:
                        _LOGGER.debug("Connection attempt result for %s:%s: %s", host, port, connected)
                        _LOGGER.error("Failed to connect to %s:%s after connection attempt.", host, port)
                        raise SigenergyModbusError(f"Failed to connect to {host}:{port}")

                    self._connected[key] = True
                    _LOGGER.info("Connected to Sigenergy system at %s:%s", host, port)

        return self._clients[key]
    async def async_connect(self, device_info: dict) -> None:
        """Connect to the Modbus device using device_info dict."""
        key = self._get_connection_key(device_info)
        await self._get_client(device_info)
        if not self._connected.get(key, False):
            host, port = key
            raise SigenergyModbusError(
                f"Failed to establish connection to device at {host}:{port}"
            )

    async def async_close(self) -> None:
        """Close all Modbus connections."""
        for key, client in self._clients.items():
            if client and self._connected.get(key, False):
                host, port = key
                async with self._locks[key]:
                    _LOGGER.debug("Attempting to close connection to %s:%s", host, port)
                    client.close()
                    self._connected[key] = False
                    _LOGGER.debug("Connection closed for %s:%s", host, port)
                    _LOGGER.info("Disconnected from Sigenergy system at %s:%s", host, port)

    def _validate_register_response(self, result: Any,
                                    register_def: ModbusRegisterDefinition) -> bool:
        """Validate if register response indicates support for the register."""
        # Handle error responses silently - these indicate unsupported registers
        if result is None or (hasattr(result, 'isError') and result.isError()):
            _LOGGER.debug("Register validation failed for address"
                          f" {register_def.address} with error: %s", result)
            return False

        registers = getattr(result, 'registers', [])
        if not registers:
            _LOGGER.debug("Register validation failed for address %s: empty response",
                          register_def.address)
            return False

        # For string type registers, check if all values are 0 (indicating no support)
        if register_def.data_type == DataType.STRING:
            _LOGGER.debug(
                "Register validation failed for address %s: string type "
                "(not all string registers have to be filled)",
                register_def.address
            )
            return not all(reg == 0 for reg in registers)

        # For numeric registers, check if values are within reasonable bounds
        try:
            value = self._decode_value(registers, register_def.data_type, register_def.gain)
            if isinstance(value, (int, float)):
                # Consider register supported if value is non-zero and within reasonable bounds
                # This helps filter out invalid/unsupported registers that might return garbage
                max_reasonable = {
                    "voltage": 1000,  # 1000V
                    "current": 1000,  # 1000A
                    "power": 1000,     # 1000kW
                    "energy": 10000000, # 10000MWh
                    "temperature": 200, # 200°C
                    "percentage": 120  # 120% Some batteries can go above 100% when charging
                }

                # Determine max value based on unit if present
                if register_def.unit:
                    unit = register_def.unit.lower()
                    if any(u in unit for u in ["v", "volt"]):
                        return 0 <= abs(value) <= max_reasonable["voltage"]
                    elif any(u in unit for u in ["a", "amp"]):
                        return 0 <= abs(value) <= max_reasonable["current"]
                    elif any(u in unit for u in ["wh", "kwh"]):
                        return 0 <= abs(value) <= max_reasonable["energy"]
                    elif any(u in unit for u in ["w", "watt"]):
                        return 0 <= abs(value) <= max_reasonable["power"]
                    elif any(u in unit for u in ["c", "f", "temp"]):
                        return -50 <= value <= max_reasonable["temperature"]
                    elif "%" in unit:
                        return 0 <= value <= max_reasonable["percentage"]
                # Default validation - accept any value including 0
                return True

            return True
        except Exception as ex:
            _LOGGER.debug("Register validation failed with exception: %s", ex)
            return False

    async def _probe_single_register(
        self,
        client: AsyncModbusTcpClient,
        slave_id: int,
        name: str,
        register: ModbusRegisterDefinition,
        device_info_log: str # Added for logging context
    ) -> Tuple[str, bool, Optional[Exception]]:
        """Probe a single register and return its name, support status, and any exception."""

        with _suppress_pymodbus_logging(really_suppress= False if _LOGGER.isEnabledFor(logging.DEBUG) else True):
            if register.register_type == RegisterType.READ_ONLY:
                result = await client.read_input_registers(
                    address=register.address,
                    count=register.count,
                    slave=slave_id
                )
            elif register.register_type == RegisterType.HOLDING:
                result = await client.read_holding_registers(
                    address=register.address,
                    count=register.count,
                    slave=slave_id
                )
            else:
                _LOGGER.debug(
                    "Register %s (0x%04X) for slave %d (%s) has unsupported type: %s",
                    name, register.address, slave_id, device_info_log, register.register_type
                )
                return name, False, None # Mark as unsupported, no exception

            is_supported = self._validate_register_response(result, register)

            # if _LOGGER.isEnabledFor(logging.DEBUG) and not is_supported:
            #     _LOGGER.debug(
            #         "Register %s (%s) for device %s is not supported. Result: %s, registers: %s",
            #         name, register.address, device_info_log, str(result), str(register)
            #     )
            return name, is_supported, None # Return name, support status, no exception

    async def async_probe_registers(
        self,
        device_info: Dict[str, str | int],
        register_defs: Dict[str, ModbusRegisterDefinition]
    ) -> None:
        """Probe registers concurrently to determine which ones are supported."""
        slave_id_value = device_info.get(CONF_SLAVE_ID)
        if slave_id_value is None:
            raise ValueError(f"Slave ID is missing in device info: {device_info}")
        slave_id = int(slave_id_value)
        client = await self._get_client(device_info)
        key = self._get_connection_key(device_info)
        device_info_log = f"{key[0]}:{key[1]}@{slave_id}" # For logging

        tasks = []
        try:
            async with self._locks[key]:
                # Create tasks for probing each register
                for name, register in register_defs.items():
                    # Only probe if support status is unknown (None)
                    if register.is_supported is None:
                        tasks.append(
                            self._probe_single_register(client, slave_id, name, register, device_info_log)
                        )
        except Exception as ex:
            _LOGGER.error("Error while preparing register probing tasks for %s: %s",
                          device_info_log, ex)
            # Mark all probed registers as potentially unsupported due to the error
            for name, register in register_defs.items():
                if register.is_supported is None:
                    register.is_supported = False
            return

        if not tasks:
            _LOGGER.debug("No registers need probing for %s.", device_info_log)
            return # Nothing to probe

        _LOGGER.debug("Probing %d registers concurrently for %s...", len(tasks), device_info_log)

        # Run probing tasks concurrently within the lock for this connection
        results = []
        try:
            async with self._locks[key]:
                # Use return_exceptions=True to prevent one failure from stopping others
                results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            _LOGGER.warning("Register probing for %s was cancelled.", device_info_log)
            # Mark remaining unknown registers as potentially unsupported due to cancellation
            for name, register in register_defs.items():
                if register.is_supported is None:
                    register.is_supported = False # Assume unsupported if probe was cancelled
            raise # Re-raise CancelledError
        except Exception as ex:
            _LOGGER.error("Unexpected error during concurrent register probing for %s: %s",
                          device_info_log, ex)
            # Mark all probed registers as potentially unsupported due to the gather error
            for name, register in register_defs.items():
                if register.is_supported is None: # Only update those that were being probed
                    register.is_supported = False
            self._connected[key] = False # Assume connection issue
            return # Exit probing on major error

        _LOGGER.debug("Finished probing for %s. Processing %d results.",
                      device_info_log, len(results))

        # Process results
        connection_error_occurred = False
        for result in results:
            if isinstance(result, Exception):
                # Handle exceptions raised by gather itself or _probe_single_register
                _LOGGER.error("Error during register probe task for %s: %s",
                              device_info_log, result)
                # If it's a connection error, mark the connection as potentially bad
                if isinstance(result, (ConnectionException, asyncio.TimeoutError,
                                       SigenergyModbusError)):
                    connection_error_occurred = True
                # We don't know which register failed here, so we can't mark it specifically.
                # The registers remain is_supported=None and will be retried on read.
                continue # Skip to next result

            # Unpack successful results
            if isinstance(result, tuple) and len(result) == 3:
                name, is_supported, probe_exception = result
                if name in register_defs:
                    register_defs[name].is_supported = is_supported
                    if probe_exception:
                        # Log the specific exception caught by _probe_single_register
                        _LOGGER.debug("Probe failed for re
