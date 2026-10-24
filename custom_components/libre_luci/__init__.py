"""The Libre Wireless (LUCI) integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .luci import LuciClient, LuciError

PLATFORMS = [Platform.MEDIA_PLAYER]

type LibreConfigEntry = ConfigEntry[LuciClient]


async def async_setup_entry(hass: HomeAssistant, entry: LibreConfigEntry) -> bool:
    """Connect to the module and set up the media player."""
    client = LuciClient(entry.data[CONF_HOST])
    try:
        await client.start()
    except LuciError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LibreConfigEntry) -> bool:
    """Close the LUCI connection."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
    return unloaded
