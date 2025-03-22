"""Config flow for Sigenergy ESS integration."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_AC_CHARGER_SLAVE_ID,
    CONF_AC_CHARGER_CONNECTIONS,
    CONF_DC_CHARGER_SLAVE_ID,
    CONF_DEVICE_TYPE,
    CONF_INVERTER_SLAVE_ID,
    CONF_INVERTER_CONNECTIONS,
    CONF_PARENT_INVERTER_ID,
    CONF_PARENT_PLANT_ID,
    CONF_PLANT_ID,
    CONF_SLAVE_ID,
    DEFAULT_PORT,
    DEFAULT_PLANT_SLAVE_ID,
    DEFAULT_INVERTER_SLAVE_ID,
    DEVICE_TYPE_NEW_PLANT,
    DEVICE_TYPE_PLANT,
    DEVICE_TYPE_INVERTER,
    DEVICE_TYPE_AC_CHARGER,
    DEVICE_TYPE_DC_CHARGER,
    DOMAIN,
    STEP_DEVICE_TYPE,
    STEP_PLANT_CONFIG,
    STEP_INVERTER_CONFIG,
    STEP_AC_CHARGER_CONFIG,
    STEP_SELECT_PLANT,
    STEP_SELECT_INVERTER,
    DEFAULT_READ_ONLY,
    CONF_READ_ONLY,
)

_LOGGER = logging.getLogger(__name__)

# Schema definitions for each step
STEP_DEVICE_TYPE_SCHEMA = vol.Schema(
    {
        vol.Required("device_type"): vol.In(
            {
                DEVICE_TYPE_NEW_PLANT: "New Plant",
                DEVICE_TYPE_INVERTER: "Inverter",
                DEVICE_TYPE_AC_CHARGER: "AC Charger",
                DEVICE_TYPE_DC_CHARGER: "DC Charger",
            }
        ),
    }
)

STEP_PLANT_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_INVERTER_SLAVE_ID, default=DEFAULT_INVERTER_SLAVE_ID): int,
        vol.Required(CONF_READ_ONLY, default=DEFAULT_READ_ONLY): bool,
    }
)

STEP_INVERTER_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_SLAVE_ID, default=DEFAULT_INVERTER_SLAVE_ID): int,
    }
)

STEP_AC_CHARGER_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_SLAVE_ID): int,
    }
)

STEP_DC_CHARGER_CONFIG_SCHEMA = vol.Schema({})

def validate_slave_ids(raw_ids: str, field_name: str) -> Tuple[List[int], Dict[str, str]]:
    """Validate slave IDs from a comma-separated string.
    
    Returns:
        Tuple containing list of valid IDs and dict of errors (if any)
        An empty string input will return an empty list with no errors.
    """
    errors = {}
    id_list = []
    
    # Skip validation for empty strings (indicating no devices of this type)
    if not raw_ids or raw_ids.strip() == "":
        return [], errors
    
    for part in raw_ids.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            val = int(part)
            if not (1 <= val <= 246):
                errors[field_name] = "each_id_must_be_between_1_and_246"
                break
            id_list.append(val)
        else:
            errors[field_name] = "invalid_integer_value"
            break
            
    # Check for duplicate IDs
    if not errors and not _LOGGER.isEnabledFor(logging.DEBUG):
        if len(set(id_list)) != len(id_list):
            errors[field_name] = "duplicate_ids_found"
            
    return id_list, errors

@config_entries.HANDLERS.register(DOMAIN)
class SigenergyConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Sigenergy ESS."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data = {}
        self._plants = {}
        self._inverters = {}
        self._selected_plant_entry_id = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # Load existing plants
        await self._async_load_plants()
        
        # If no plants exist, go directly to plant configuration
        if not self._plants:
            self._data[CONF_DEVICE_TYPE] = DEVICE_TYPE_NEW_PLANT
            return await self.async_step_plant_config()
        
        # Otherwise, show device type selection
        return await self.async_step_device_type()
        
    async def async_step_device_type(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the device type selection."""
        if user_input is None:
            return self.async_show_form(
                step_id=STEP_DEVICE_TYPE,
                data_schema=STEP_DEVICE_TYPE_SCHEMA,
            )
        
        device_type = user_input["device_type"]
        self._data[CONF_DEVICE_TYPE] = device_type
        
        if device_type == DEVICE_TYPE_NEW_PLANT:
            return await self.async_step_plant_config()
        elif device_type in [DEVICE_TYPE_INVERTER, DEVICE_TYPE_AC_CHARGER, DEVICE_TYPE_DC_CHARGER]:
            return await self.async_step_select_plant()
        
        # Should never reach here
        return self.async_abort(reason="unknown_device_type")
    
    async def async_step_plant_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the plant configuration step."""
        errors = {}
        
        if user_input is None:
            return self.async_show_form(
                step_id=STEP_PLANT_CONFIG,
                data_schema=STEP_PLANT_CONFIG_SCHEMA
            )

        # Store plant configuration
        self._data.update(user_input)
        
        # Always use the default plant ID (247)
        self._data[CONF_PLANT_ID] = DEFAULT_PLANT_SLAVE_ID

        # Process and validate inverter ID
        try:
            inverter_id = int(user_input[CONF_INVERTER_SLAVE_ID])
            if not (1 <= inverter_id <= 246):
                errors[CONF_INVERTER_SLAVE_ID] = "each_id_must_be_between_1_and_246"
            elif not _LOGGER.isEnabledFor(logging.DEBUG):
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_INVERTER:
                        existing_id = entry.data.get(CONF_INVERTER_SLAVE_ID)
                        if existing_id and inverter_id in existing_id:
                            errors[CONF_INVERTER_SLAVE_ID] = "duplicate_ids_found"
                            break
        except (ValueError, TypeError):
            errors[CONF_INVERTER_SLAVE_ID] = "invalid_integer_value"
            
        if errors:
            return self.async_show_form(
                step_id=STEP_PLANT_CONFIG,
                data_schema=STEP_PLANT_CONFIG_SCHEMA,
                errors=errors
            )

        # Store the validated lists
        self._data[CONF_INVERTER_SLAVE_ID] = [inverter_id]
        self._data[CONF_AC_CHARGER_SLAVE_ID] = []
        self._data[CONF_DC_CHARGER_SLAVE_ID] = []
        
        # Store the plant name generated based on the number of installed plants
        plant_no = len(self._plants)
        self._data[CONF_NAME] = f"Sigen{' ' if plant_no == 0 else f' {plant_no} '}Plant"
        
        # Set the device type as plant
        self._data[CONF_DEVICE_TYPE] = DEVICE_TYPE_PLANT

        # Create the configuration entry with the default name
        return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
    
    async def async_step_select_plant(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the plant selection step."""
        if not self._plants:
            # No plants available, abort with error
            return self.async_abort(reason="no_plants_available")
        
        if user_input is None:
            # Create schema with plant selection
            schema = vol.Schema({
                vol.Required(CONF_PARENT_PLANT_ID): vol.In(self._plants)
            })
            
            return self.async_show_form(
                step_id=STEP_SELECT_PLANT,
                data_schema=schema,
            )
        
        # Store the selected plant ID
        self._selected_plant_entry_id = user_input[CONF_PARENT_PLANT_ID]
        self._data[CONF_PARENT_PLANT_ID] = self._selected_plant_entry_id
        
        # Get the plant entry to access its configuration
        plant_entry = self.hass.config_entries.async_get_entry(self._selected_plant_entry_id)
        if plant_entry:
            # Copy host and port from the plant
            self._data[CONF_HOST] = plant_entry.data.get(CONF_HOST)
            self._data[CONF_PORT] = plant_entry.data.get(CONF_PORT)
        
        # Proceed based on the device type
        device_type = self._data.get(CONF_DEVICE_TYPE)
        
        if device_type == DEVICE_TYPE_INVERTER:
            return await self.async_step_inverter_config()
        elif device_type == DEVICE_TYPE_AC_CHARGER:
            return await self.async_step_ac_charger_config()
        elif device_type == DEVICE_TYPE_DC_CHARGER:
            # For DC Charger, we need to load inverters from the selected plant
            await self._async_load_inverters(self._selected_plant_entry_id)
            return await self.async_step_select_inverter()
        
        # Should never reach here
        return self.async_abort(reason="unknown_device_type")
    async def async_step_inverter_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the inverter configuration step."""
        errors = {}
        
        if user_input is None:
            # For first inverter, use plant's host and port
            if self._data.get(CONF_HOST) and self._data.get(CONF_PORT):
                schema = vol.Schema({
                    vol.Required(CONF_HOST, default=self._data[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=self._data[CONF_PORT]): int,
                    vol.Required(CONF_SLAVE_ID, default=DEFAULT_INVERTER_SLAVE_ID): int,
                })
            else:
                schema = STEP_INVERTER_CONFIG_SCHEMA
            return self.async_show_form(
                step_id=STEP_INVERTER_CONFIG,
                data_schema=schema,
            )
        
        # Validate the slave ID
        slave_id = user_input.get(CONF_SLAVE_ID)
        if slave_id is None or not (1 <= slave_id <= 246):
            errors[CONF_SLAVE_ID] = "each_id_must_be_between_1_and_246"
            return self.async_show_form(
                step_id=STEP_INVERTER_CONFIG,
                data_schema=STEP_INVERTER_CONFIG_SCHEMA,
                errors=errors,
            )
            
        # Check for duplicate IDs
        plant_entry = self.hass.config_entries.async_get_entry(self._selected_plant_entry_id)
        if plant_entry:
            plant_inverters = plant_entry.data.get(CONF_INVERTER_SLAVE_ID, [])
            if slave_id in plant_inverters and not _LOGGER.isEnabledFor(logging.DEBUG):
                errors[CONF_SLAVE_ID] = "duplicate_ids_found"
                return self.async_show_form(
                    step_id=STEP_INVERTER_CONFIG,
                    data_schema=STEP_INVERTER_CONFIG_SCHEMA,
                    errors=errors,
                )
            
            # Get the inverter name based on number of existing inverters
            inverter_no = len(plant_inverters)
            inverter_name = f"Inverter{' ' if inverter_no == 0 else f' {inverter_no + 1} '}"
            
            # Create or update the inverter connections dictionary
            new_data = dict(plant_entry.data)
            inverter_connections = new_data.get(CONF_INVERTER_CONNECTIONS, {})
            inverter_connections[inverter_name] = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_SLAVE_ID: slave_id
            }
            
            # Update the plant's configuration with the new inverter
            new_data[CONF_INVERTER_SLAVE_ID] = plant_inverters + [slave_id]
            new_data[CONF_INVERTER_CONNECTIONS] = inverter_connections
            
            self.hass.config_entries.async_update_entry(
                plant_entry,
                data=new_data
            )
            
            return self.async_abort(reason="device_added")
            
        return self.async_abort(reason="parent_plant_not_found")
    
    async def async_step_ac_charger_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the AC charger configuration step."""
        errors = {}
        
        if user_input is None:
            return self.async_show_form(
                step_id=STEP_AC_CHARGER_CONFIG,
                data_schema=STEP_AC_CHARGER_CONFIG_SCHEMA,
            )
        
        # Validate the slave ID
        slave_id = user_input.get(CONF_SLAVE_ID)
        if slave_id is None or not (1 <= slave_id <= 246):
            errors[CONF_SLAVE_ID] = "each_id_must_be_between_1_and_246"
            return self.async_show_form(
                step_id=STEP_AC_CHARGER_CONFIG,
                data_schema=STEP_AC_CHARGER_CONFIG_SCHEMA,
                errors=errors,
            )
            
        # Check for duplicate IDs and conflicts with inverters
        plant_entry = self.hass.config_entries.async_get_entry(self._selected_plant_entry_id)
        if plant_entry:
            plant_ac_chargers = plant_entry.data.get(CONF_AC_CHARGER_SLAVE_ID, [])
            plant_inverters = plant_entry.data.get(CONF_INVERTER_SLAVE_ID, [])
            
            if slave_id in plant_ac_chargers:
                errors[CONF_SLAVE_ID] = "duplicate_ids_found"
            elif slave_id in plant_inverters:
                errors[CONF_SLAVE_ID] = "ac_charger_conflicts_inverter"
                
            if errors:
                return self.async_show_form(
                    step_id=STEP_AC_CHARGER_CONFIG,
                    data_schema=STEP_AC_CHARGER_CONFIG_SCHEMA,
                    errors=errors,
                )
            
            # Get the AC charger name based on number of existing AC chargers
            ac_charger_no = len(plant_ac_chargers)
            ac_charger_name = f"AC Charger{' ' if ac_charger_no == 0 else f' {ac_charger_no + 1} '}"
            
            # Create or update the AC charger connections dictionary
            new_data = dict(plant_entry.data)
            ac_charger_connections = new_data.get(CONF_AC_CHARGER_CONNECTIONS, {})
            ac_charger_connections[ac_charger_name] = {
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_SLAVE_ID: slave_id
            }
            
            # Update the plant's configuration with the new AC charger
            new_data[CONF_AC_CHARGER_SLAVE_ID] = plant_ac_chargers + [slave_id]
            new_data[CONF_AC_CHARGER_CONNECTIONS] = ac_charger_connections
            
            self.hass.config_entries.async_update_entry(
                plant_entry,
                data=new_data
            )
            
            return self.async_abort(reason="device_added")
            
        return self.async_abort(reason="parent_plant_not_found")
    
    async def async_step_select_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the inverter selection step for DC chargers."""
        if not self._inverters:
            # No inverters available, abort with error
            return self.async_abort(reason="no_inverters_available")
        
        if user_input is None:
            # Create schema with inverter selection
            schema = vol.Schema({
                vol.Required(CONF_PARENT_INVERTER_ID): vol.In(self._inverters)
            })
            
            return self.async_show_form(
                step_id=STEP_SELECT_INVERTER,
                data_schema=schema,
            )
        
        # Get the selected inverter
        parent_inverter_id = user_input[CONF_PARENT_INVERTER_ID]
        inverter_entry = self.hass.config_entries.async_get_entry(parent_inverter_id)
        if not inverter_entry:
            return self.async_abort(reason="parent_inverter_not_found")
            
        # Get the slave ID and verify it exists
        inverter_slave_id = inverter_entry.data.get(CONF_SLAVE_ID)
        if not inverter_slave_id:
            return self.async_abort(reason="parent_inverter_invalid")
            
        # Check for existing DC charger with this ID
        plant_entry = self.hass.config_entries.async_get_entry(self._selected_plant_entry_id)
        if plant_entry:
            plant_dc_chargers = plant_entry.data.get(CONF_DC_CHARGER_SLAVE_ID, [])
            
            if inverter_slave_id in plant_dc_chargers:
                return self.async_abort(reason="duplicate_ids_found")
                
            # Update the plant's configuration with the new DC charger
            new_data = dict(plant_entry.data)
            new_data[CONF_DC_CHARGER_SLAVE_ID] = plant_dc_chargers + [inverter_slave_id]
            
            self.hass.config_entries.async_update_entry(
                plant_entry,
                data=new_data
            )
            
            return self.async_abort(reason="device_added")
            
        return self.async_abort(reason="parent_plant_not_found")

    async def _async_load_plants(self) -> None:
        """Load existing plants from config entries."""
        self._plants = {}
        
        # Log the number of config entries for debugging
        _LOGGER.debug("Total config entries: %s", len(self.hass.config_entries.async_entries(DOMAIN)))
        
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            # Log each entry to see what's being found
            _LOGGER.debug("Found entry: %s, device type: %s", 
                        entry.entry_id, 
                        entry.data.get(CONF_DEVICE_TYPE))
                        
            if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PLANT:
                self._plants[entry.entry_id] = entry.data.get(CONF_NAME, f"Plant {entry.entry_id}")
                
        # Log the plants that were found
        _LOGGER.debug("Found plants: %s", self._plants)
    
    async def _async_load_inverters(self, plant_entry_id: str) -> None:
        """Load existing inverters for a specific plant."""
        self._inverters = {}
        
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_INVERTER and
                entry.data.get(CONF_PARENT_PLANT_ID) == plant_entry_id):
                self._inverters[entry.entry_id] = entry.data.get(CONF_NAME, f"Inverter {entry.entry_id}")
        
        _LOGGER.debug("Found inverters for plant %s: %s", plant_entry_id, self._inverters)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle dynamic reconfiguration of inverter and AC charger slave IDs."""
        if user_input is None:
            # Create schema with current values
            schema = self._create_reconfigure_schema()
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=schema,
            )

        # Process and validate inverter ID
        errors = {}
        try:
            inverter_id = int(user_input[CONF_INVERTER_SLAVE_ID])
            if not (1 <= inverter_id <= 246):
                errors[CONF_INVERTER_SLAVE_ID] = "each_id_must_be_between_1_and_246"
            elif not _LOGGER.isEnabledFor(logging.DEBUG):
                # Check for duplicate IDs
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_INVERTER:
                        existing_id = entry.data.get(CONF_INVERTER_SLAVE_ID)
                        if existing_id and inverter_id in existing_id:
                            errors[CONF_INVERTER_SLAVE_ID] = "duplicate_ids_found"
                            break
        except (ValueError, TypeError):
            errors[CONF_INVERTER_SLAVE_ID] = "invalid_integer_value"

        if errors:
            # Re-create schema with user input values for error display
            schema = self._create_reconfigure_schema(
                user_input.get(CONF_INVERTER_SLAVE_ID, "")
            )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=schema,
                errors=errors
            )

        # Update the configuration entry with the new data
        new_data = {
            **self._data,
            CONF_INVERTER_SLAVE_ID: [inverter_id],
            CONF_AC_CHARGER_SLAVE_ID: [],
            CONF_DC_CHARGER_SLAVE_ID: [],
        }

        # Ensure device type is preserved
        device_type = self._data.get(CONF_DEVICE_TYPE)
        if device_type:
            new_data[CONF_DEVICE_TYPE] = device_type
        
        # Update the configuration entry
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=new_data
        )
        
        return self.async_create_entry(title="", data={})

    def _create_reconfigure_schema(self, inv_ids=""):
        """Create schema for reconfiguration step with default or provided values."""
        # Use provided values or get current values from data
        if not inv_ids:
            current_ids = self._data.get(CONF_INVERTER_SLAVE_ID, [])
            inv_ids = ", ".join(str(i) for i in current_ids) if current_ids else ""
        
        return vol.Schema({
            vol.Required(CONF_INVERTER_SLAVE_ID, default=inv_ids): str,
        })

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return SigenergyOptionsFlowHandler(config_entry)

class SigenergyOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Sigenergy options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self._data = dict(config_entry.data)
        self._plants = {}
        self._inverters = {}

    def _create_reconfigure_schema(self, inv_ids: str = "") -> vol.Schema:
        """Create schema for reconfiguration step with default or provided values."""
        if not inv_ids:
            current_ids = self._data.get(CONF_INVERTER_SLAVE_ID, [])
            inv_ids = ", ".join(str(i) for i in current_ids) if current_ids else ""
        
        return vol.Schema({
            vol.Required(CONF_INVERTER_SLAVE_ID, default=inv_ids): str,
        })

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the options for the custom component."""
        device_type = self._data.get(CONF_DEVICE_TYPE)
        
        if device_type == DEVICE_TYPE_PLANT:
            return await self.async_step_plant_options(user_input)
        elif device_type == DEVICE_TYPE_INVERTER:
            return await self.async_step_inverter_options(user_input)
        elif device_type == DEVICE_TYPE_AC_CHARGER:
            return await self.async_step_ac_charger_options(user_input)
        elif device_type == DEVICE_TYPE_DC_CHARGER:
            return await self.async_step_dc_charger_options(user_input)
        
        # Default fallback to reconfigure step for backward compatibility
        return await self.async_step_reconfigure(user_input)
    
    async def async_step_plant_options(self, user_input: dict[str, Any] | None = None):
        """Handle plant options."""
        # For now, just use the reconfigure step for plants
        return await self.async_step_reconfigure(user_input)
    
    async def async_step_inverter_options(self, user_input: dict[str, Any] | None = None):
        """Handle inverter options."""
        # For now, just use the reconfigure step for inverters
        return await self.async_step_reconfigure(user_input)
    
    async def async_step_ac_charger_options(self, user_input: dict[str, Any] | None = None):
        """Handle AC charger options."""
        # For now, just use the reconfigure step for AC chargers
        return await self.async_step_reconfigure(user_input)
    
    async def async_step_dc_charger_options(self, user_input: dict[str, Any] | None = None):
        """Handle DC charger options."""
        # For now, just use the reconfigure step for DC chargers
        return await self.async_step_reconfigure(user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration of inverter and AC charger slave IDs."""
        errors = {}
        
        if user_input is None:
            # Create schema with current values
            schema = self._create_reconfigure_schema()
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=schema,
            )

        # Process and validate inverter ID
        try:
            inverter_id = int(user_input[CONF_INVERTER_SLAVE_ID])
            if not (1 <= inverter_id <= 246):
                errors[CONF_INVERTER_SLAVE_ID] = "each_id_must_be_between_1_and_246"
        except (ValueError, TypeError):
            errors[CONF_INVERTER_SLAVE_ID] = "invalid_integer_value"


        if errors:
            # Re-create schema with user input values for error display
            schema = self._create_reconfigure_schema(
                user_input.get(CONF_INVERTER_SLAVE_ID, "")
            )
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=schema,
                errors=errors,
            )

        # Ensure we preserve the device type (especially important if it's DEVICE_TYPE_PLANT)
        device_type = self._data.get(CONF_DEVICE_TYPE)
        
        # Update the configuration entry with the new IDs
        new_data = {
            **self._data,
            CONF_INVERTER_SLAVE_ID: [inverter_id],
            CONF_AC_CHARGER_SLAVE_ID: [],
            CONF_DC_CHARGER_SLAVE_ID: [],
        }
        
        # Ensure device type is preserved
        if device_type:
            new_data[CONF_DEVICE_TYPE] = device_type
            
        self.hass.config_entries.async_update_entry(
            self.config_entry, data=new_data
        )
        
        return self.async_create_entry(title="", data={})
