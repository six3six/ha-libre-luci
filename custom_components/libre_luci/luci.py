"""Asyncio client for the LUCI protocol of Libre Wireless modules (TCP 7777).

LUCI is the control channel of Libre Wireless streaming modules (LS9 and relatives): every source
app on the module (AirPlay, Google Cast, Spotify Connect, DLNA, Roon, Bluetooth...) reports its state
to a central `luci_service`, so one connection gives a unified player state.

Frames: 10-byte header then data. Requests are little-endian
`remote_id:u16 type:u8 mid:u16 status:u8 crc:u16 len:u16`; replies use the same layout big-endian.
A client must register with message box (MB) 3 before anything else is answered.

Safety: `luci_service` has no supervisor, and its pre-processing reads the payload of some message
boxes regardless of the request type. An empty GET on 0x64/0x6A/0x6C/0xD7 dereferences NULL and kills
the service until the speaker reboots; a GET on 0x65/0x66/0x71/0x8D/0x8E changes the multiroom state.
This client therefore only sends the registration, GETs from `SAFE_GET` and two validated SETs
(volume 0x40, play control 0x28). Every other message box is refused before it reaches the socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

PORT = 7777
GET, SET = 1, 2
MB_REGISTER = 0x03
MB_PLAY_CONTROL = 0x28
MB_VOLUME = 0x40

HEADER = struct.Struct(">HBHBHH")
REQUEST = struct.Struct("<HBHBHH")
MAX_PAYLOAD = 0x1400

SOURCES = {
    0: "None",
    1: "AirPlay",
    2: "DLNA",
    3: "DMP",
    4: "Spotify",
    5: "USB",
    6: "SD card",
    8: "vTuner",
    9: "TuneIn",
    10: "Miracast",
    12: "Multiroom",
    14: "Aux",
    18: "QPlay",
    19: "Bluetooth",
    21: "Deezer",
    22: "Tidal",
    23: "Favourites",
    24: "Google Cast",
    27: "Roon",
    28: "Alexa",
}

# MB 0x33 / PlayView "PlayState"
PLAY_STATES = {0: "playing", 1: "stopped", 2: "paused", 3: "connecting", 4: "receiving", 5: "buffering"}

# Strings forwarded by mediaserver to OnPlayControlEvent of the active source. Same vocabulary
# (strcmp) in spotifyhifi, airplay_v2, castlucicomm and libreraat_app.
PLAY_COMMANDS = ("PLAY", "PAUSE", "RESUME", "STOP", "NEXT", "PREV", "MUTE", "UNMUTE")

# Message boxes that are safe to GET (checked one by one on firmware p15245).
SAFE_GET = (0x05, 0x06, 0x07, 0x09, 0x18, 0x2A, 0x2C, 0x2F, 0x32, 0x33, 0x3F, 0x40, 0x5A, 0x67)

MB_FIRMWARE = 0x05
MB_HOST_FIRMWARE = 0x06
MB_CAST_VERSION = 0x07
MB_STANDBY = 0x18
MB_REMOTE_UI = 0x2A
MB_REMOTE_UI_PLAY = 0x2D
MB_POSITION = 0x31
MB_SOURCE = 0x32
MB_PLAY_STATE = 0x33
MB_MUTE = 0x3F
MB_NAME = 0x5A
MB_MULTIROOM = 0x67


class LuciError(Exception):
    """Communication with the LUCI service failed."""


def encode_request(kind: int, mid: int, data: bytes = b"") -> bytes:
    """Build a request frame, refusing anything outside the whitelists."""
    allowed = (
        (kind == SET and mid == MB_REGISTER)
        or (kind == GET and mid in SAFE_GET and not data)
        or (kind == SET and mid == MB_VOLUME and data.isdigit() and 0 <= int(data) <= 100)
        or (kind == SET and mid == MB_PLAY_CONTROL and data.decode(errors="replace") in PLAY_COMMANDS)
    )
    if not allowed:
        raise ValueError(f"LUCI message box 0x{mid:X} type {kind} is not whitelisted")
    return REQUEST.pack(0, kind, mid, 0, 0, len(data)) + data


def _to_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class LuciState:
    """Unified player state of a Libre module."""

    connected: bool = False
    name: str | None = None
    firmware: str | None = None
    host_firmware: str | None = None
    cast_version: str | None = None
    standby: bool | None = None
    source: int | None = None
    play_state: int | None = None
    volume: int | None = None
    muted: bool | None = None
    multiroom: str | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    image_url: str | None = None
    uri: str | None = None
    duration_ms: int | None = None
    position_ms: int | None = None
    position_updated_at: float | None = None
    sample_rate: int | None = None
    bit_depth: int | None = None
    bitrate_kbps: int | None = None

    @property
    def source_name(self) -> str | None:
        if self.source is None:
            return None
        return SOURCES.get(self.source, f"Source {self.source}")

    @property
    def play_state_name(self) -> str | None:
        if self.play_state is None:
            return None
        return PLAY_STATES.get(self.play_state, "unknown")


class LuciClient:
    """Persistent LUCI connection with push updates and automatic reconnect."""

    def __init__(self, host: str, port: int = PORT, resync_interval: float = 300.0) -> None:
        self.host = host
        self.port = port
        self.state = LuciState()
        self._resync_interval = resync_interval
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._listeners: list[Callable[[], None]] = []
        self._write_lock = asyncio.Lock()
        self._closing = False

    # -- connection -------------------------------------------------------------------------

    async def connect(self, timeout: float = 5.0) -> None:
        """Open the connection, register and request the full state."""
        try:
            self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)
            self._writer.write(encode_request(SET, MB_REGISTER))
            await self._writer.drain()
            async with asyncio.timeout(timeout):
                while True:
                    _kind, mid, status, _data = await self._read_frame()
                    if mid == MB_REGISTER:
                        if status != 1:
                            raise LuciError(f"registration refused (status {status})")
                        break
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as err:
            await self._close_transport()
            raise LuciError(f"cannot connect to {self.host}:{self.port}: {err!r}") from err
        self.state.connected = True
        await self._request_all()

    async def start(self) -> None:
        """Connect, read the initial state, then keep the connection alive in the background."""
        await self.connect()
        await self._read_initial_state()
        self._task = asyncio.create_task(self._run(), name=f"luci {self.host}")

    async def _read_initial_state(self, timeout: float = 3.0) -> None:
        """Consume the replies to the initial GETs until name and firmware are known."""
        try:
            async with asyncio.timeout(timeout):
                while self.state.name is None or self.state.firmware is None:
                    self._apply(*await self._read_frame())
        except TimeoutError:
            pass
        except (OSError, asyncio.IncompleteReadError) as err:
            await self._close_transport()
            self.state.connected = False
            raise LuciError(f"connection to {self.host} lost during setup: {err!r}") from err

    async def stop(self) -> None:
        self._closing = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._close_transport()

    async def _close_transport(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _run(self) -> None:
        backoff = 5.0
        while not self._closing:
            try:
                if self._reader is None:
                    await self.connect()
                    backoff = 5.0
                    self._notify()
                await self._read_loop()
            except (LuciError, OSError, asyncio.IncompleteReadError, TimeoutError) as err:
                _LOGGER.debug("LUCI connection to %s lost: %r", self.host, err)
            await self._close_transport()
            if self.state.connected:
                self.state.connected = False
                self._notify()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _read_loop(self) -> None:
        next_resync = time.monotonic() + self._resync_interval
        while True:
            timeout = max(1.0, next_resync - time.monotonic())
            try:
                frame = await asyncio.wait_for(self._read_frame(), timeout)
            except TimeoutError:
                await self._request_all()
                next_resync = time.monotonic() + self._resync_interval
                continue
            if self._apply(*frame):
                self._notify()

    async def _read_frame(self) -> tuple[int, int, int, bytes]:
        assert self._reader is not None
        header = await self._reader.readexactly(HEADER.size)
        _remote, kind, mid, status, _crc, length = HEADER.unpack(header)
        if length > MAX_PAYLOAD:
            raise LuciError(f"oversized frame ({length} bytes)")
        data = await self._reader.readexactly(length) if length else b""
        return kind, mid, status, data

    async def _send(self, kind: int, mid: int, data: bytes = b"") -> None:
        frame = encode_request(kind, mid, data)
        if self._writer is None:
            raise LuciError("not connected")
        async with self._write_lock:
            self._writer.write(frame)
            await self._writer.drain()

    async def _request_all(self) -> None:
        for mid in SAFE_GET:
            await self._send(GET, mid)

    # -- commands ---------------------------------------------------------------------------

    async def set_volume(self, volume: int) -> None:
        await self._send(SET, MB_VOLUME, str(max(0, min(100, int(volume)))).encode())

    async def play_control(self, command: str) -> None:
        await self._send(SET, MB_PLAY_CONTROL, command.upper().encode())

    # -- state ------------------------------------------------------------------------------

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    def _apply(self, kind: int, mid: int, status: int, data: bytes) -> bool:
        """Update the state from one frame; return True when something visible changed.

        The module repeats every notification once per local subscriber, and empty frames are
        acknowledgements or empty boxes (status 2): both are ignored.
        """
        if not data or status not in (0, 1):
            return False
        raw = data.decode("utf-8", "replace")
        if mid == MB_POSITION:
            return self._apply_position(_to_int(raw))
        if mid in (MB_REMOTE_UI, MB_REMOTE_UI_PLAY):
            return self._apply_remote_ui(raw)
        updates: dict[str, Any] = {}
        if mid == MB_NAME:
            updates["name"] = raw
        elif mid == MB_FIRMWARE:
            updates["firmware"] = raw
        elif mid == MB_HOST_FIRMWARE:
            updates["host_firmware"] = raw
        elif mid == MB_CAST_VERSION:
            updates["cast_version"] = raw
        elif mid == MB_STANDBY:
            updates["standby"] = raw == "1"
        elif mid == MB_SOURCE:
            updates["source"] = _to_int(raw)
        elif mid == MB_PLAY_STATE:
            updates["play_state"] = _to_int(raw)
        elif mid == MB_VOLUME:
            updates["volume"] = _to_int(raw)
        elif mid == MB_MUTE:
            updates["muted"] = raw.upper() == "MUTE"
        elif mid == MB_MULTIROOM:
            updates["multiroom"] = raw
        else:
            return False
        return self._update(updates)

    def _update(self, updates: dict[str, Any]) -> bool:
        s = self.state
        changed = False
        for key, value in updates.items():
            if getattr(s, key) != value:
                if key == "play_state":
                    # Sources stop pushing the position while paused: freeze it where it really is.
                    s.position_ms, s.position_updated_at = self._current_position(), time.time()
                setattr(s, key, value)
                changed = True
        return changed

    def _current_position(self) -> int | None:
        """Position now, extrapolated from the last known one while playing."""
        s = self.state
        if s.position_ms is None or s.position_updated_at is None or s.play_state != 0:
            return s.position_ms
        return s.position_ms + int((time.time() - s.position_updated_at) * 1000)

    def _apply_position(self, position: int | None) -> bool:
        """Position is pushed every second while playing: only report jumps (seek, new track)."""
        s = self.state
        if position is None:
            return False
        expected = self._current_position()
        jumped = expected is None or abs(position - expected) > 2000
        if jumped:
            s.position_ms = position
            s.position_updated_at = time.time()
        return jumped

    def _apply_remote_ui(self, raw: str) -> bool:
        try:
            ui = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(ui, dict) or ui.get("Title") != "PlayView":
            return False
        w = ui.get("Window CONTENTS") or {}
        updates = {
            "title": w.get("TrackName") or None,
            "artist": w.get("Artist") or None,
            "album": w.get("Album") or None,
            "image_url": w.get("CoverArtUrl") or None,
            "uri": w.get("PlayUrl") or None,
            "duration_ms": duration if (duration := _to_int(w.get("TotalTime"))) and duration > 0 else None,
            "sample_rate": _to_int(w.get("SampleRate")),
            "bit_depth": _to_int(w.get("BitDepth")),
            "bitrate_kbps": _to_int(w.get("BitRate")),
        }
        if (source := _to_int(w.get("Current Source"))) is not None:
            updates["source"] = source
        if (play_state := _to_int(w.get("PlayState"))) is not None:
            updates["play_state"] = play_state
        new_track = updates["uri"] != self.state.uri or updates["title"] != self.state.title
        changed = self._update(updates)
        if new_track:
            self.state.position_ms = 0
            self.state.position_updated_at = time.time()
        return changed


async def async_probe(host: str, port: int = PORT, timeout: float = 5.0) -> LuciState:
    """Connect once, collect the state for a short while and disconnect (config flow check)."""
    client = LuciClient(host, port)
    await client.connect(timeout)
    try:
        await client._read_initial_state()
    finally:
        await client.stop()
    if client.state.firmware is None:
        raise LuciError(f"{host} registered but did not report a firmware version")
    return client.state
