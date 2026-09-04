"""Compatibility helpers for Home Assistant device hierarchy registration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def _registry_api(registry_api: Any | None) -> Any:
    """Return an injected or runtime Home Assistant device-registry module."""
    if registry_api is not None:
        return registry_api

    from homeassistant.helpers import device_registry as dr

    return dr


def parent_device_info(
    hass: Any,
    config_entry_id: str,
    identifier: tuple[str, str],
    *,
    registry_api: Any | None = None,
) -> dict[str, Any]:
    """Return a parent link supported by the running Home Assistant version."""
    api = _registry_api(registry_api)
    lookup = getattr(api, "async_get_device_id_by_identifier", None)
    if lookup is None:
        return {"via_device": identifier}

    return {
        "via_device_id": lookup(
            hass,
            identifier,
            config_entry_id=config_entry_id,
        )
    }


def register_parent_devices(
    hass: Any,
    *,
    config_entry_id: str,
    domain: str,
    plant_name: str,
    inverter_connections: Mapping[str, Any],
    coordinator_data: Mapping[str, Any],
    device_id_fn: Callable[[str], str],
    registry_api: Any | None = None,
) -> None:
    """Register plant and inverter parents before child platforms are set up."""
    api = _registry_api(registry_api)
    device_registry = api.async_get(hass)
    plant_identifier = (domain, f"{config_entry_id}_plant")
    device_registry.async_get_or_create(
        config_entry_id=config_entry_id,
        identifiers={plant_identifier},
        name=plant_name,
        manufacturer="Sigenergy",
        model="Energy Storage System",
    )

    for device_name in inverter_connections:
        inverter_data = coordinator_data.get("inverters", {}).get(device_name, {})
        inverter_identifier = (
            domain,
            f"{config_entry_id}_{device_id_fn(device_name)}",
        )
        device_registry.async_get_or_create(
            config_entry_id=config_entry_id,
            identifiers={inverter_identifier},
            name=device_name,
            manufacturer="Sigenergy",
            model=inverter_data.get("inverter_model_type", "Sigen Inverter"),
            serial_number=inverter_data.get("inverter_serial_number"),
            sw_version=inverter_data.get("inverter_machine_firmware_version"),
            **parent_device_info(
                hass,
                config_entry_id,
                plant_identifier,
                registry_api=api,
            ),
        )
