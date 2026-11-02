"""Optional per-entry display-name language using Home Assistant translations."""
from __future__ import annotations

from collections.abc import Mapping
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_ENTITY_NAME_LANGUAGE,
    DOMAIN,
    ENTITY_NAME_LANGUAGE_AUTO,
    ENTITY_NAME_LANGUAGES,
)

_LOGGER = logging.getLogger(__name__)


async def async_load_entity_name_translations(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, str] | None:
    """Load once before creating entities; Automatic keeps HA's normal naming."""
    language = entry.options.get(CONF_ENTITY_NAME_LANGUAGE, ENTITY_NAME_LANGUAGE_AUTO)
    if language == ENTITY_NAME_LANGUAGE_AUTO:
        return None
    if language not in ENTITY_NAME_LANGUAGES:
        _LOGGER.warning("Unsupported entity name language; using Home Assistant's server language")
        return None
    return await async_get_translations(hass, language, "entity", {DOMAIN})


def translated_entity_name(
    translations: Mapping[str, str], platform: str, key: str,
    placeholders: Mapping[str, str],
) -> str | None:
    """Resolve only the name, retaining HA's own fallback when one is missing."""
    template = translations.get(f"component.{DOMAIN}.entity.{platform}.{key}.name")
    if template is None:
        return None
    try:
        return template.format(**placeholders)
    except (KeyError, ValueError):
        _LOGGER.warning("Invalid display-name translation for %s.%s; using the default name", platform, key)
        return None
