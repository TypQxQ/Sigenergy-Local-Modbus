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
from pymodbus.payload import BinaryPayloadBuilder
from pymodbus.client.mixin import ModbusClientMixin
from pymodbus import __version__ as pymodbus_version

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

        # Log to dev logger the version of pymodbus being used
        _LOGGER.debug("Using pymodbus version: %s", pymodbus_version)

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
                        _LOGGER.debug("Probe failed for register %s on %s: %s",
                                      name, device_info_log, probe_exception)
                        if isinstance(probe_exception, (ConnectionException, asyncio.TimeoutError,
                                                        SigenergyModbusError)):
                            connection_error_occurred = True

        # If any connection-related error occurred during probing, mark the connection state
        if connection_error_occurred:
            _LOGGER.warning("Connection errors encountered during probing for %s. "
                            "Marking connection as potentially disconnected.", device_info_log)
            self._connected[key] = False


        _LOGGER.debug("Probing completed for %s. Supported registers: %s", device_info_log,
                      {name: register.is_supported for name, register in register_defs.items() \
                       if register.is_supported is not None})

    async def async_read_registers(
        self,
        device_info: Dict[str, Any], # Changed from slave_id
        address: int,
        count: int,
        register_type: RegisterType
    ) -> Optional[List[int]]:
        """Read registers from the Modbus device."""
        slave_id_value = device_info.get(CONF_SLAVE_ID)
        if slave_id_value is None:
            _LOGGER.error("Slave ID missing in device info for read operation: %s", device_info)
            # Return None as per function signature, indicating read failure
            return None
        slave_id = int(slave_id_value)

        try:
            client = await self._get_client(device_info)
            key = self._get_connection_key(device_info)

            async with self._locks[key]:
                with _suppress_pymodbus_logging(really_suppress= False if _LOGGER.isEnabledFor(logging.DEBUG) else True):
                    result = await client.read_input_registers(
                        address=address, count=count, slave=slave_id
                    ) if register_type == RegisterType.READ_ONLY \
                        else await client.read_holding_registers(
                        address=address, count=count, slave=slave_id
                    )

                    if result.isError():
                        # Do NOT mark connection as closed for specific register read errors
                        _LOGGER.debug("Modbus read error for %s:%s@%s (address %s): %s.", key[0], key[1], slave_id, address, result)
                        # self._connected[key] = False # Removed this line
                        return None # Indicate read failure for this register
                    return result.registers

        except (ConnectionException, asyncio.TimeoutError) as ex: # Catch TimeoutError here too
            key = self._get_connection_key(device_info)
            _LOGGER.warning("ConnectionException/Timeout during read for %s:%s@%s (address %s): %s. Marking connection as closed.", key[0], key[1], slave_id, address, ex)
            self._connected[key] = False # Mark disconnected only on actual connection issues
            raise SigenergyModbusError(f"Connection error: {ex}") from ex
        except ModbusException as ex:
            raise SigenergyModbusError(f"Modbus error: {ex}") from ex
        except Exception as ex:
            raise SigenergyModbusError(f"Error reading registers: {ex}") from ex

    async def async_write_register(
        self,
        device_info: dict,
        address: int,
        value: int,
        register_type: RegisterType
    ) -> None:
        """Write a single register to the Modbus device."""
        try:
            slave_id_value = device_info.get(CONF_SLAVE_ID)
            if slave_id_value is None:
                raise ValueError(f"Slave ID is missing in device info: {device_info}")
            slave_id = int(slave_id_value)
            client = await self._get_client(device_info)
            key = self._get_connection_key(device_info)

            async with self._locks[key]:
                if register_type in [RegisterType.HOLDING, RegisterType.WRITE_ONLY]:
                    # Try multiple approaches to write to the register
                    approaches = []

                    # Always try direct addressing approaches
                    approaches.append({
                        "description": f"write_registers with direct addressing ({address})",
                        "function": "write_registers",
                        "address": address,
                        "values": [value]
                    })
                    approaches.append({
                        "description": f"write_register with direct addressing ({address})",
                        "function": "write_register",
                        "address": address,
                        "value": value
                    })

                    # If address >= 40001, also try offset addressing
                    if address >= 40001:
                        offset_address = address - 40001
                        approaches.append({
                            "description": f"write_registers with offset addressing \
                                ({offset_address})",
                            "function": "write_registers",
                            "address": offset_address,
                            "values": [value]
                        })
                        approaches.append({
                            "description": f"write_register with offset addressing \
                                ({offset_address})",
                            "function": "write_register",
                            "address": offset_address,
                            "value": value
                        })

                    # Try each approach until one succeeds
                    last_error = None
                    success = False

                    for i, approach in enumerate(approaches):
                        try:
                            _LOGGER.debug(
                                "Attempt %d: Using %s for register %s with value %s for slave %s",
                                i+1, approach["description"], address, value, slave_id
                            )

                            if approach["function"] == "write_registers":
                                result = await client.write_registers(
                                    address=approach["address"],
                                    values=approach["values"],
                                    slave=slave_id
                                )
                            else:  # write_register
                                result = await client.write_register(
                                    address=approach["address"],
                                    value=approach["value"],
                                    slave=slave_id
                                )

                            if not result.isError():
                                _LOGGER.debug("Success with approach: %s", approach["description"])
                                success = True
                                break

                            _LOGGER.debug("Error with approach %s: %s", approach["description"],
                                          result)
                            last_error = result

                        except Exception as ex:
                            _LOGGER.debug("Exception with approach %s: %s", approach["description"],
                                          ex)
                            last_error = ex

                    # Check if any approach succeeded
                    if success:
                        _LOGGER.debug("Successfully wrote to register at address %s", address)
                        return

                    # If we've tried all approaches and still have an error
                    self._connected[key] = False
                    _LOGGER.warning("Modbus write error for %s:%s@%s (address %s): %s. Marking connection as closed.", key[0], key[1], slave_id, address, last_error)
                    if last_error:
                        _LOGGER.debug("All write attempts failed. Final error: %s", last_error)
                        if isinstance(last_error, Exception):
                            # Re-raise the exception
                            raise last_error
                        else:
                            # It's a Modbus error response
                            raise SigenergyModbusError(
                                f"Error writing register at address {address}: {last_error}"
                            )
                else:
                    raise SigenergyModbusError(
                        f"Register type {register_type} is not writable"
                    )
        except ConnectionException as ex:
            key = self._get_connection_key(device_info)
            _LOGGER.warning("ConnectionException during write for %s:%s@%s (address %s): %s. Marking connection as closed.", key[0], key[1], slave_id, address, ex)
            self._connected[key] = False
            raise SigenergyModbusError(f"Connection error: {ex}") from ex
        except ModbusException as ex:
            raise SigenergyModbusError(f"Modbus error: {ex}") from ex
        except Exception as ex:
            raise SigenergyModbusError(f"Error writing register: {ex}") from ex

    async def async_write_registers(
        self,
        device_info: dict,
        address: int,
        values: List[int],
        register_type: RegisterType
    ) -> None:
        """Write multiple registers to the Modbus device."""
        try:
            slave_id_value = device_info.get(CONF_SLAVE_ID)
            if slave_id_value is None:
                raise ValueError(f"Slave ID is missing in device info: {device_info}")
            slave_id = int(slave_id_value)
            client = await self._get_client(device_info)
            key = self._get_connection_key(device_info)

            async with self._locks[key]:
                if register_type in [RegisterType.HOLDING, RegisterType.WRITE_ONLY]:
                    tried_offset = False
                    last_error = None
                    _LOGGER.debug(
                        "Trying write_registers at original address %s with values %s for slave %s",
                        address, values, slave_id
                    )
                    result = await client.write_registers(
                        address=address, values=values, slave=slave_id
                    )
                    if result.isError():
                        _LOGGER.warning("Modbus write_registers error for %s:%s@%s (address %s): %s. Marking connection as closed.", key[0], key[1], slave_id, address, result)
                        self._connected[key] = False
                        _LOGGER.debug("Error response from write_registers: %s", result)
                        raise SigenergyModbusError(
                            f"Error writing registers at address {address}: {result if not tried_offset else last_error}, {result}"
                        )
                    else:
                        _LOGGER.debug("Successfully wrote to registers at address %s", address)
                else:
                    raise SigenergyModbusError(
                        f"Register type {register_type} is not writable"
                    )
        except ConnectionException as ex:
            key = self._get_connection_key(device_info)
            _LOGGER.warning("ConnectionException during write_registers for %s:%s@%s (address %s): %s. Marking connection as closed.", key[0], key[1], slave_id, address, ex)
            self._connected[key] = False
            raise SigenergyModbusError(f"Connection error: {ex}") from ex
        except ModbusException as ex:
            raise SigenergyModbusError(f"Modbus error: {ex}") from ex
        except Exception as ex:
            raise SigenergyModbusError(f"Error writing registers: {ex}") from ex

    def _decode_value(
        self,
        registers: List[int],
        data_type: DataType,
        gain: float
    ) -> Union[int, float, str]:
        """Decode register values based on data type."""
        if data_type == DataType.U16:
            value = ModbusClientMixin.convert_from_registers(
                registers, data_type=ModbusClientMixin.DATATYPE.UINT16
            )
        elif data_type == DataType.S16:
            value = 0
            value = ModbusClientMixin.convert_from_registers(
                registers, data_type=ModbusClientMixin.DATATYPE.INT16
            )
        elif data_type == DataType.U32:
            value = 0
            value = ModbusClientMixin.convert_from_registers(
                registers, data_type=ModbusClientMixin.DATATYPE.UINT32
            )
        elif data_type == DataType.S32:
            value = 0
            value = ModbusClientMixin.convert_from_registers(
                registers, data_type=ModbusClientMixin.DATATYPE.INT32
            )
        elif data_type == DataType.U64:
            value = 0
            value = ModbusClientMixin.convert_from_registers(
                registers, data_type=ModbusClientMixin.DATATYPE.UINT64
            )
        elif data_type == DataType.STRING:
            # return value  # No gain for strings
            return ModbusClientMixin.convert_from_registers(
                registers,
                data_type=ModbusClientMixin.DATATYPE.STRING)  # type: ignore[no-untyped-call]
        else:
            raise SigenergyModbusError(f"Unsupported data type: {data_type}")

        # Apply gain
        if isinstance(value, (int, float)) and gain != 1:
            value = value / gain

        return value  # type: ignore[no-untyped-return]

    def _encode_value(
        self,
        value: Union[int, float, str],
        data_type: DataType,
        gain: float
    ) -> List[int]:
        """Encode value to register values based on data type."""
        # For simple U16 values like 0 or 1, just return the value directly
        # This bypasses potential byte order issues with the BinaryPayloadBuilder
        if data_type == DataType.U16 and isinstance(value, int) and 0 <= value <= 255:
            _LOGGER.debug("Using direct value encoding for simple U16 value: %s", value)
            return [value]

        # For other cases, use the BinaryPayloadBuilder
        builder = BinaryPayloadBuilder(byteorder=Endian.BIG, wordorder=Endian.BIG)

        # Apply gain for numeric values
        if isinstance(value, (int, float)) and gain != 1 and data_type != DataType.STRING:
            value = int(value * gain)

        _LOGGER.debug("Encoding value %s with data_type %s", value, data_type)

        if data_type == DataType.U16:
            builder.add_16bit_uint(int(value))
        elif data_type == DataType.S16:
            builder.add_16bit_int(int(value))
        elif data_type == DataType.U32:
            builder.add_32bit_uint(int(value))
        elif data_type == DataType.S32:
            builder.add_32bit_int(int(value))
        elif data_type == DataType.U64:
            builder.add_64bit_uint(int(value))
        elif data_type == DataType.STRING:
            builder.add_string(str(value))
        else:
            raise SigenergyModbusError(f"Unsupported data type: {data_type}")

        registers = builder.to_registers()
        _LOGGER.debug("Encoded registers: %s", registers)
        return registers

    async def _async_read_device_data_core(
        self,
        device_info: Dict[str, Any], # Changed from slave_id
        device_name: str,
        device_type_log_prefix: str,
        registers_to_read: Dict[str, ModbusRegisterDefinition]
    ) -> Dict[str, Any]:
        """Core logic for reading device data registers."""

        data = {}
        for register_name, register_def in registers_to_read.items():
            if register_def.is_supported is not False:  # Read if supported or unknown
                try:
                    registers = await self.async_read_registers(
                        device_info=device_info,
                        address=register_def.address,
                        count=register_def.count,
                        register_type=register_def.register_type,
                    )

                    if registers is None:
                        data[register_name] = None
                        # If probing failed or wasn't done, and read fails, mark as unsupported
                        if register_def.is_supported is None:
                            register_def.is_supported = False
                        continue

                    value = self._decode_value(
                        registers=registers,
                        data_type=register_def.data_type,
                        gain=register_def.gain,
                    )

                    data[register_name] = value
                    # _LOGGER.debug("Read register %s = %s from %s '%s'",
                    # register_name, value, device_type_log_prefix, device_name)

                    # If we successfully read a register that wasn't probed/confirmed,
                    # mark it as supported
                    if register_def.is_supported is None:
                        register_def.is_supported = True

                except Exception as ex:
                    _LOGGER.error(
                        "Error reading %s '%s' register %s: %s",
                        device_type_log_prefix, device_name, register_name, ex
                    )
                    # Check if the exception indicates a connection issue
                    if isinstance(ex, (ConnectionException, SigenergyModbusError)) and 'connect' in str(ex).lower():
                        key = self._get_connection_key(device_info)
                        if self._connected.get(key, True): # Only log if not already marked disconnected
                            _LOGGER.warning("Connection error detected during data read for %s '%s'. Marking connection as closed.", device_type_log_prefix, device_name)
                            self._connected[key] = False
                    data[register_name] = None
                    # If this is the first time we fail to read this register,
                    # mark it as unsupported
                    if register_def.is_supported is None:
                        register_def.is_supported = False
        return data

    async def async_read_plant_data(self, update_frequency:
                                    UpdateFrequencyType=UpdateFrequencyType.LOW
                                    ) -> Dict[str, Any]:
        """Read all supported plant data."""
        # Probe registers if not done yet
        if not self.plant_registers_probed:
            try:
                plant_info = self.config_entry.data.get(CONF_PLANT_CONNECTION, {})
                await self.async_probe_registers(plant_info, PLANT_RUNNING_INFO_REGISTERS)
                # Also probe parameter registers that can be read
                await self.async_probe_registers(plant_info, {
                    name: reg for name, reg in PLANT_PARAMETER_REGISTERS.items()
                    if reg.register_type != RegisterType.WRITE_ONLY
                })
                self.plant_registers_probed = True
            except Exception as ex:
                _LOGGER.error("Failed to probe plant registers: %s", ex)
                # Continue with reading, some registers might still work

        # Filter running info registers
        running_regs = {
            name: reg for name, reg in PLANT_RUNNING_INFO_REGISTERS.items()
            if reg.update_frequency >= update_frequency
        }
        # Filter parameter registers
        param_regs = {
            name: reg for name, reg in PLANT_PARAMETER_REGISTERS.items()
            if reg.register_type != RegisterType.WRITE_ONLY and
               reg.update_frequency >= update_frequency
        }
        # Merge the dictionaries
        registers_to_read = {**running_regs, **param_regs}
        _LOGGER.debug("Reading %s Plant registers. update_frequency is %s",
                      len(registers_to_read), update_frequency)

        # Use the core reading logic
        plant_info = self.config_entry.data.get(CONF_PLANT_CONNECTION, {})
        plant_info.setdefault(CONF_SLAVE_ID, self.plant_id)
        return await self._async_read_device_data_core(
            device_info=plant_info,
            device_name="plant",
            device_type_log_prefix="plant",
            registers_to_read=registers_to_read
        )

    async def async_read_inverter_data(self, inverter_name: str, update_frequency:
                                       UpdateFrequencyType=UpdateFrequencyType.LOW
                                       ) -> Dict[str, Any]:
        """Read all supported inverter data."""
        # Look up inverter details by name
        if inverter_name not in self.inverter_connections:
            _LOGGER.error("Unknown inverter name provided for reading data: %s", inverter_name)
            return {} # Return empty dict if inverter name is not found
        inverter_info = self.inverter_connections[inverter_name]

        # Probe registers if not done yet for this inverter
        if inverter_name not in self.inverter_registers_probed:
            try:
                await self.async_probe_registers(inverter_info, INVERTER_RUNNING_INFO_REGISTERS)
                # Also probe parameter registers that can be read
                await self.async_probe_registers(inverter_info, {
                    name: reg for name, reg in INVERTER_PARAMETER_REGISTERS.items()
                    if reg.register_type != RegisterType.WRITE_ONLY
                })
                self.inverter_registers_probed.add(inverter_name)
            except Exception as ex:
                _LOGGER.error("Failed to probe inverter '%s' registers: %s", inverter_name, ex)
                # Continue with reading, some registers might still work

        # Read registers from both running info and parameter registers
        registers_to_read = {}
        registers_to_read = {
            **{name: reg for name, reg in INVERTER_RUNNING_INFO_REGISTERS.items()
            if reg.register_type != RegisterType.WRITE_ONLY and
            reg.update_frequency >= update_frequency },
            **{name: reg for name, reg in INVERTER_PARAMETER_REGISTERS.items()
            if reg.register_type != RegisterType.WRITE_ONLY and
            reg.update_frequency >= update_frequency },
            # **{name: reg for name, reg in DC_CHARGER_RUNNING_INFO_REGISTERS.items()
            # if reg.register_type != RegisterType.WRITE_ONLY and
            # reg.update_frequency >= update_frequency },
            # **{name: reg for name, reg in DC_CHARGER_PARAMETER_REGISTERS.items()
            # if reg.register_type != RegisterType.WRITE_ONLY and
            # reg.update_frequency >= update_frequency },
        }
        _LOGGER.debug("Reading %s Inverter registers. update_frequency is %s",
                      len(registers_to_read), update_frequency)

        # Use the core reading logic
        return await self._async_read_device_data_core(
            device_info=inverter_info,
            device_name=inverter_name,
            device_type_log_prefix="inverter",
            registers_to_read=registers_to_read
        )

    async def async_read_dc_charger_data(self, inverter_name: str, update_frequency:
                                       UpdateFrequencyType=UpdateFrequencyType.LOW
                                       ) -> Dict[str, Any]:
        """Read all supported DC charger data."""
        # Look up inverter details by name
        if inverter_name not in self.inverter_connections:
            _LOGGER.error("Unknown inverter name provided for reading DC Charger data: %s", inverter_name)
            return {} # Return empty dict if inverter name is not found
        inverter_info = self.inverter_connections[inverter_name]

        # Probe registers if not done yet for this inverter
        if inverter_name not in self.dc_charger_registers_probed:
            try:
                await self.async_probe_registers(inverter_info, DC_CHARGER_RUNNING_INFO_REGISTERS)
                # Also probe parameter registers that can be read
                await self.async_probe_registers(inverter_info, {
                    name: reg for name, reg in DC_CHARGER_PARAMETER_REGISTERS.items()
                    if reg.register_type != RegisterType.WRITE_ONLY
                })
                self.dc_charger_registers_probed.add(inverter_name)
            except Exception as ex:
                _LOGGER.error("Failed to probe dc-charger '%s' registers: %s", inverter_name, ex)
                # Continue with reading, some registers might still work

        # Read registers from both running info and parameter registers
        registers_to_read = {}
        registers_to_read = {
            # **{name: reg for name, reg in INVERTER_RUNNING_INFO_REGISTERS.items()
            # if reg.register_type != RegisterType.WRITE_ONLY and
            # reg.update_frequency >= update_frequency },
            # **{name: reg for name, reg in INVERTER_PARAMETER_REGISTERS.items()
            # if reg.register_type != RegisterType.WRITE_ONLY and
            # reg.update_frequency >= update_frequency },
            **{name: reg for name, reg in DC_CHARGER_RUNNING_INFO_REGISTERS.items()
            if reg.register_type != RegisterType.WRITE_ONLY and
            reg.update_frequency >= update_frequency },
            **{name: reg for name, reg in DC_CHARGER_PARAMETER_REGISTERS.items()
            if reg.register_type != RegisterType.WRITE_ONLY and
            reg.update_frequency >= update_frequency },
        }
        _LOGGER.debug("Reading %s DC charger registers. update_frequency is %s",
                      len(registers_to_read), update_frequency)

        # Use the core reading logic
        return await self._async_read_device_data_core(
            device_info=inverter_info,
            device_name=inverter_name,
            device_type_log_prefix="dc charger",
            registers_to_read=registers_to_read
        )

    async def async_read_ac_charger_data(self, ac_charger_name: str, update_frequency:
                                          UpdateFrequencyType=UpdateFrequencyType.LOW
                                          ) -> Dict[str, Any]:
        """Read all supported AC charger data."""
        # Look up AC charger details by name
        if ac_charger_name not in self.ac_charger_connections:
            _LOGGER.error("Unknown AC charger name provided for reading data: %s", ac_charger_name)
            return {}  # Return empty dict if AC charger name is not found

        ac_charger_info = self.ac_charger_connections[ac_charger_name]

        # Probe registers if not done yet for this AC charger
        if ac_charger_name not in self.ac_charger_registers_probed:
            try:
                await self.async_probe_registers(ac_charger_info, AC_CHARGER_RUNNING_INFO_REGISTERS)
                # Also probe parameter registers that can be read
                await self.async_probe_registers(ac_charger_info, {
                    name: reg for name, reg in AC_CHARGER_PARAMETER_REGISTERS.items()
                    if reg.register_type != RegisterType.WRITE_ONLY
                })
                self.ac_charger_registers_probed.add(ac_charger_name)
            except Exception as ex:
                _LOGGER.error("Failed to probe AC charger '%s' registers: %s", ac_charger_name, ex)
                # Continue with reading, some registers might still work

        # Read registers from both running info and parameter registers
        registers_to_read = {}
        registers_to_read = {
            **{name: reg for name, reg in AC_CHARGER_RUNNING_INFO_REGISTERS.items()
               if reg.register_type != RegisterType.WRITE_ONLY and
               reg.update_frequency >= update_frequency},
            **{name: reg for name, reg in AC_CHARGER_PARAMETER_REGISTERS.items()
               if reg.register_type != RegisterType.WRITE_ONLY and
               reg.update_frequency >= update_frequency}
        }
        _LOGGER.debug("Reading %s AC charger registers. update_frequency is %s",
                      len(registers_to_read), update_frequency)

        # Use the core reading logic
        return await self._async_read_device_data_core(
            device_info=ac_charger_info,
            device_name=ac_charger_name,
            device_type_log_prefix="AC charger",
            registers_to_read=registers_to_read
        )

    async def async_write_parameter(
        self,
        device_type: str,
        device_identifier: Optional[str],
        register_name: str,
        value: Union[int, float, str]
    ) -> None:
        """Write a parameter to a specified device (plant, inverter, or AC charger).

        Args:
            device_type: The type of device ('plant', 'inverter', 'ac_charger').
            device_identifier: The name of the inverter or AC charger, or None for the plant.
            register_name: The name of the parameter register to write.
            value: The value to write to the register.

        Raises:
            SigenergyModbusError: If writing is disabled (read-only mode), the device/parameter
                                  is unknown, slave ID is missing, or a Modbus error occurs.
            ValueError: If device_type is invalid or device_identifier is missing when required.
        """
        if self.read_only:
            _LOGGER.error("Cannot write parameter while in read-only mode")
            return

        slave_id: Optional[int] = None
        parameter_registers: Dict[str, ModbusRegisterDefinition] = {}
        connection_dict: Optional[Dict[str, Any]] = None # Type hint correction

        # Determine slave ID and parameter dictionary based on device type
        if device_type == "plant":
            connection_dict = self.plant_connection
            # Ensure plant_connection is treated as a dict for type checking
            parameter_registers = PLANT_PARAMETER_REGISTERS
        elif device_type == "inverter":
            if not device_identifier:
                raise ValueError("device_identifier is required for device_type 'inverter'")
            connection_dict = self.inverter_connections
            parameter_registers = INVERTER_PARAMETER_REGISTERS
        elif device_type == "ac_charger":
            if not device_identifier:
                raise ValueError("device_identifier is required for device_type 'ac_charger'")
            connection_dict = self.ac_charger_connections
            parameter_registers = AC_CHARGER_PARAMETER_REGISTERS
        elif device_type == "dc_charger":
            if not device_identifier:
                raise ValueError("device_identifier is required for device_type 'dc_charger'")
            connection_dict = self.inverter_connections
            parameter_registers = DC_CHARGER_PARAMETER_REGISTERS
        else:
            raise ValueError(f"Unknown device_type: {device_type}")

        _LOGGER.debug(
            "Writing %s parameter '%s' with value %s to device '%s'",
            device_type, register_name, value, device_identifier or 'plant'
        )

        # Get device_info and slave_id
        device_info: Dict[str, Any] = {}
        if connection_dict is not None:
            if device_type == "plant":
                # Plant uses the main connection dict directly
                device_info = connection_dict
            elif device_identifier:
                # Inverter/AC Charger look up by identifier
                if device_identifier not in connection_dict:
                    raise SigenergyModbusError(f"Unknown {device_type} name: {device_identifier}")
                # Ensure the looked-up value is a dict
                potential_device_info = connection_dict.get(device_identifier)
                if isinstance(potential_device_info, dict):
                    device_info = potential_device_info
                else:
                    raise SigenergyModbusError(f"Configuration for {device_type} \
                                               '{device_identifier}' is not a valid dictionary.")

        slave_id = device_info.get(CONF_SLAVE_ID)

        # Safety check: Ensure slave_id was determined
        if slave_id is None:
             # Try getting plant_id if it's the plant device
            if device_type == "plant":
                slave_id = self.plant_id
            if slave_id is None: # Still None after checking plant_id
                raise SigenergyModbusError("Could not determine slave ID for " +\
                                           f"{device_type} '{device_identifier or 'plant'}' " +\
                                            f"from configuration: {device_info}")

        # Ensure slave_id is added to device_info if missing (needed by write functions)
        if CONF_SLAVE_ID not in device_info:
            device_info[CONF_SLAVE_ID] = slave_id

        # Get register definition
        if register_name not in parameter_registers:
            raise SigenergyModbusError(f"Unknown {device_type} parameter: {register_name}")
        register_def = parameter_registers[register_name]

        _LOGGER.debug(
            "Writing %s parameter '%s' (device: %s, slave: %s) with value %s \
                to address %s (type: %s, data_type: %s, gain: %s)",
            device_type, register_name, device_identifier or 'plant',
            slave_id, value, register_def.address,
            register_def.register_type, register_def.data_type, register_def.gain
        )
        encoded_values = self._encode_value(
            value=value,
            data_type=register_def.data_type,
            gain=register_def.gain,
        )
        _LOGGER.debug("Encoded values for %s '%s': %s", device_type, register_name, encoded_values)

        # Use the existing high-level write methods which handle locks/clients internally
        try:
            if len(encoded_values) == 1:
                await self.async_write_register(
                    device_info=device_info, # Pass the correctly typed device_info
                    address=register_def.address,
                    value=encoded_values[0],
                    register_type=register_def.register_type,
                )
            else:
                await self.async_write_registers(
                    device_info=device_info, # Pass the correctly typed device_info
                    address=register_def.address,
                    values=encoded_values,
                    register_type=register_def.register_type,
                )
        except SigenergyModbusError as ex:
            _LOGGER.error("Failed to write %s parameter '%s' (device: %s): %s",
                          device_type, register_name, device_identifier or 'plant', ex)
            raise # Re-raise the specific error
