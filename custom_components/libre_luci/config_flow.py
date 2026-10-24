"""Config flow for Libre Wireless (LUCI): Bonjour discovery and manual host."""

from __future__ import annotations

import json
import logging
import plistlib
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import CONF_MAC, CONF_MANUFACTURER, CONF_MODEL, DOMAIN
from .luci import LuciError, LuciState, async_probe

_LOGGER = logging.getLogger(__name__)


async def _async_device_identity(session: aiohttp.ClientSession, host: str) -> dict[str, str]:
    """MAC, manufacturer and model of a module whose Bonjour record we did not see.

    LUCI does not expose the MAC address, but the AirPlay receiver of the module does (`GET /info`,
    binary plist), and so does the Google Cast receiver (`/setup/eureka_info`).
    """
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with session.get(f"http://{host}:7000/info", timeout=timeout) as resp:
            info = plistlib.loads(await resp.read())
        if info.get("deviceID"):
            return {
                CONF_MAC: info["deviceID"],
                CONF_MANUFACTURER: info.get("manufacturer", ""),
                CONF_MODEL: info.get("model", ""),
            }
    except (aiohttp.ClientError, TimeoutError, plistlib.InvalidFileException, ValueError) as err:
        _LOGGER.debug("AirPlay /info of %s unavailable: %r", host, err)
    try:
        async with session.get(f"http://{host}:8008/setup/eureka_info?params=mac_address", timeout=timeout) as resp:
            info = json.loads(await resp.text())
        if info.get("mac_address"):
            return {CONF_MAC: info["mac_address"], CONF_MANUFACTURER: "", CONF_MODEL: ""}
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Cast eureka_info of %s unavailable: %r", host, err)
    return {}


class LibreLuciConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Libre Wireless modules."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._identity: dict[str, str] = {}
        self._state: LuciState | None = None

    def _create_entry(self) -> ConfigFlowResult:
        assert self._state is not None
        return self.async_create_entry(
            title=self._state.name or self._identity.get(CONF_MODEL) or self._host,
            data={CONF_HOST: self._host, **self._identity},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set up a module from its IP address or host name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            try:
                self._state = await async_probe(self._host)
            except LuciError as err:
                _LOGGER.debug("LUCI probe of %s failed: %s", self._host, err)
                errors["base"] = "cannot_connect"
            else:
                self._identity = await _async_device_identity(async_get_clientsession(self.hass), self._host)
                if not self._identity:
                    errors["base"] = "no_mac"
                else:
                    self._identity[CONF_MAC] = format_mac(self._identity[CONF_MAC])
                    await self.async_set_unique_id(self._identity[CONF_MAC])
                    self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
                    return self._create_entry()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=self._host): str}),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a module found through its AirPlay Bonjour record (`_airplay._tcp`)."""
        props = discovery_info.properties
        if not (mac := props.get("deviceid")):
            return self.async_abort(reason="not_libre_device")
        self._host = str(discovery_info.ip_address)
        self._identity = {
            CONF_MAC: format_mac(mac),
            CONF_MANUFACTURER: props.get("manufacturer", ""),
            CONF_MODEL: props.get("model", ""),
        }
        await self.async_set_unique_id(self._identity[CONF_MAC])
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})
        # Many AirPlay receivers match the Bonjour filter: only Libre modules answer on LUCI.
        try:
            self._state = await async_probe(self._host)
        except LuciError:
            return self.async_abort(reason="not_libre_device")
        self.context["title_placeholders"] = {"name": self._state.name or discovery_info.name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user to confirm a discovered module."""
        assert self._state is not None
        if user_input is not None:
            return self._create_entry()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._state.name or self._host,
                "host": self._host,
                "model": " ".join(
                    part for part in (self._identity.get(CONF_MANUFACTURER), self._identity.get(CONF_MODEL)) if part
                )
                or "Libre Wireless",
            },
        )
