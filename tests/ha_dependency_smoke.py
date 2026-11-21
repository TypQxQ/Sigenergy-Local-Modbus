"""Exercise Sigenergy forms with an installed Home Assistant Core."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import probatio
from homeassistant.helpers import config_validation as cv

from custom_components.sigen.config_flow import SigenergyConfigFlow, SigenergyOptionsFlowHandler
from custom_components.sigen.const import (
    CONF_HOST,
    CONF_INVERTER_SLAVE_ID,
    CONF_PORT,
    CONF_READ_ONLY,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)


def form_field_names(form):
    """Serialize fields using the same serializer as Home Assistant's flow view."""
    fields = probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
    return {field["name"] for field in fields}


async def main():
    setup = SigenergyConfigFlow()
    setup.flow_id = "sigen-setup-smoke"
    setup.handler = DOMAIN
    assert "device_type" in form_field_names(await setup.async_step_device_type())

    plant = await setup.async_step_plant_config()
    assert CONF_SCAN_INTERVAL in form_field_names(plant)
    schema = plant["data_schema"]
    plant_input = {
        CONF_HOST: "192.0.2.1",
        CONF_PORT: 502,
        CONF_INVERTER_SLAVE_ID: 1,
        CONF_READ_ONLY: False,
        CONF_SCAN_INTERVAL: "5",
    }
    assert schema(plant_input)[CONF_SCAN_INTERVAL] == 5
    try:
        schema({**plant_input, CONF_SCAN_INTERVAL: 0})
    except probatio.Invalid:
        pass
    else:
        raise AssertionError("Scan intervals below one second must be rejected")

    options = SigenergyOptionsFlowHandler()
    options.flow_id = "sigen-options-smoke"
    options.handler = "sigen-smoke-entry"
    options.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_get_known_entry=lambda _entry_id: SimpleNamespace(options={}),
    ))
    assert "entity_name_language" in form_field_names(await options.async_step_display_settings())
    assert "power_flow_on_w" in form_field_names(await options.async_step_power_flow_settings())
    print("Sigenergy imports and its setup and options forms pass on Home Assistant")


if __name__ == "__main__":
    asyncio.run(main())
