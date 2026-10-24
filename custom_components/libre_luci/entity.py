"""Base entity: one device per Libre module, state pushed by the LUCI client."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import LibreConfigEntry
from .const import CONF_MAC, CONF_MANUFACTURER, CONF_MODEL, DOMAIN
from .luci import LuciClient


class LibreEntity(Entity):
    """Entity of a Libre Wireless module."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: LibreConfigEntry, key: str | None = None) -> None:
        self._client: LuciClient = entry.runtime_data
        mac = entry.data[CONF_MAC]
        state = self._client.state
        self._attr_unique_id = mac if key is None else f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            name=state.name or entry.title,
            manufacturer=entry.data.get(CONF_MANUFACTURER) or "Libre Wireless",
            model=entry.data.get(CONF_MODEL) or None,
            sw_version=state.firmware,
            hw_version=f"MCU {state.host_firmware}" if state.host_firmware else None,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._client.state.connected
