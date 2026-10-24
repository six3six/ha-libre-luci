"""Bluetooth pairing switch: makes the speaker discoverable for two minutes."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LibreConfigEntry
from .entity import LibreEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LibreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the Bluetooth pairing switch."""
    async_add_entities([LibreBluetoothPairingSwitch(entry)])


class LibreBluetoothPairingSwitch(LibreEntity, SwitchEntity):
    """On while the speaker is discoverable; turns itself off after 120 s or when a device connects."""

    _attr_translation_key = "bluetooth_pairing"

    def __init__(self, entry: LibreConfigEntry) -> None:
        super().__init__(entry, "bluetooth_pairing")

    @property
    def is_on(self) -> bool:
        return self._client.state.bluetooth_pairing

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._client.start_bluetooth_pairing()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.stop_bluetooth_pairing()
