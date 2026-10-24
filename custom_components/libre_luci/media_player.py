"""Media player for a Libre Wireless module, fed by LUCI push notifications."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LibreConfigEntry
from .const import CONF_MAC, CONF_MANUFACTURER, CONF_MODEL, DOMAIN
from .luci import LuciClient

PLAY_STATE_TO_HA = {
    0: MediaPlayerState.PLAYING,
    1: MediaPlayerState.IDLE,
    2: MediaPlayerState.PAUSED,
    3: MediaPlayerState.BUFFERING,
    4: MediaPlayerState.BUFFERING,
    5: MediaPlayerState.BUFFERING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LibreConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the media player of a config entry."""
    async_add_entities([LibreMediaPlayer(entry)])


class LibreMediaPlayer(MediaPlayerEntity):
    """The player state that every source of the module reports to LUCI."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_volume_step = 0.02
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, entry: LibreConfigEntry) -> None:
        self._client: LuciClient = entry.runtime_data
        mac = entry.data[CONF_MAC]
        state = self._client.state
        self._attr_unique_id = mac
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

    @property
    def state(self) -> MediaPlayerState:
        s = self._client.state
        if s.standby:
            return MediaPlayerState.STANDBY
        if not s.source:
            return MediaPlayerState.IDLE
        return PLAY_STATE_TO_HA.get(s.play_state, MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        volume = self._client.state.volume
        return None if volume is None else volume / 100

    @property
    def is_volume_muted(self) -> bool | None:
        return self._client.state.muted

    @property
    def source(self) -> str | None:
        return self._client.state.source_name

    @property
    def app_name(self) -> str | None:
        return self._client.state.source_name

    def _track(self, value: Any) -> Any:
        """Track metadata only makes sense while a source is active."""
        return value if self._client.state.source else None

    @property
    def media_title(self) -> str | None:
        return self._track(self._client.state.title)

    @property
    def media_artist(self) -> str | None:
        return self._track(self._client.state.artist)

    @property
    def media_album_name(self) -> str | None:
        return self._track(self._client.state.album)

    @property
    def media_image_url(self) -> str | None:
        return self._track(self._client.state.image_url)

    @property
    def media_content_id(self) -> str | None:
        return self._track(self._client.state.uri)

    @property
    def media_duration(self) -> int | None:
        duration = self._track(self._client.state.duration_ms)
        return None if duration is None else duration // 1000

    @property
    def media_position(self) -> int | None:
        position = self._track(self._client.state.position_ms)
        return None if position is None else position // 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        updated = self._track(self._client.state.position_updated_at)
        return None if updated is None else datetime.fromtimestamp(updated, UTC)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._client.state
        return {
            key: value
            for key, value in (
                ("sample_rate", s.sample_rate),
                ("bit_depth", s.bit_depth),
                ("bitrate_kbps", s.bitrate_kbps),
                ("multiroom", s.multiroom),
            )
            if value is not None and (key == "multiroom" or s.source)
        }

    async def async_set_volume_level(self, volume: float) -> None:
        await self._client.set_volume(round(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._client.play_control("MUTE" if mute else "UNMUTE")

    async def async_media_play(self) -> None:
        paused = self._client.state.play_state == 2
        await self._client.play_control("RESUME" if paused else "PLAY")

    async def async_media_pause(self) -> None:
        await self._client.play_control("PAUSE")

    async def async_media_stop(self) -> None:
        await self._client.play_control("STOP")

    async def async_media_next_track(self) -> None:
        await self._client.play_control("NEXT")

    async def async_media_previous_track(self) -> None:
        await self._client.play_control("PREV")
