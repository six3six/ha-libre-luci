# Libre Wireless (LUCI) for Home Assistant

[![Validate](https://github.com/six3six/ha-libre-luci/actions/workflows/validate.yml/badge.svg)](https://github.com/six3six/ha-libre-luci/actions/workflows/validate.yml)
[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=six3six&repository=ha-libre-luci&category=integration)

A media player for speakers built on a **Libre Wireless** streaming module (LS9 and relatives), such
as the **Argon Audio Solo**. It talks to the module's own control service, **LUCI** (TCP 7777), so a
single entity follows whatever is playing — AirPlay, Google Cast, Spotify Connect, DLNA, Roon or
Bluetooth — and controls it.

## Features

- **Bonjour discovery**: speakers are found from their AirPlay record and confirmed over LUCI.
- **Push updates** (`local_push`): state, volume, mute, source and track changes arrive instantly.
- **Now playing**: title, artist, album, cover art, duration, position, plus audio format
  (`sample_rate`, `bit_depth`, `bitrate_kbps`) as attributes.
- **Control**: volume (set/step), mute, play, pause, stop, next, previous.
- The device is linked to the same speaker in the Google Cast integration (shared MAC address).

| Media player feature | LUCI message |
|---|---|
| volume set / step | SET `0x40` (0–100) |
| play, pause, stop, next, previous, mute | SET `0x28` (`PLAY`/`RESUME`, `PAUSE`, `STOP`, `NEXT`, `PREV`, `MUTE`/`UNMUTE`) |
| state, source, track | push of `0x32`, `0x33`, `0x2A`/`0x2D` (PlayView), `0x31`, `0x3F`, `0x40` |

## Installation

1. HACS → ⋮ → **Custom repositories** → `https://github.com/six3six/ha-libre-luci`, category
   *Integration* (or use the button above).
2. Download **Libre Wireless (LUCI)** and restart Home Assistant.
3. The speaker shows up under **Settings → Devices & services → Discovered**. Otherwise add
   **Libre Wireless (LUCI)** and enter its IP address.

## Safety notes

LUCI has no authentication and the module's `luci_service` is fragile: it has no supervisor, and some
message boxes crash it or change multiroom state even on a read (an empty GET on `0x64`, `0x6A`,
`0x6C` or `0xD7` kills it until the speaker is power-cycled). This integration only sends the
registration, a fixed list of reads that were checked one by one, and the two validated commands
above; everything else is refused before it reaches the socket. It never reads or writes the
module's NV settings.

Each LUCI client from the same IP address shares reply routing with the others: run at most one
LUCI client per host (for example, do not run another LUCI tool on the Home Assistant host).

## Tested with

| Speaker | Module firmware | Sources tested |
|---|---|---|
| Argon Audio Solo | p15245 | Spotify Connect |

Play control strings come from the source apps of the module (Spotify, AirPlay, Google Cast, Roon
share the same vocabulary); other sources may ignore some commands.
