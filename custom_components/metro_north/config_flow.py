"""Config flow for MTA Metro North integration."""
from __future__ import annotations

import logging
from typing import Any

import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_API_KEY,
    CONF_STATIONS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    GTFS_RT_URL,
    HARLEM_LINE_STATIONS,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _validate_connection(api_key: str | None) -> None:
    """Test that we can reach the GTFS-RT feed."""
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    resp = requests.get(GTFS_RT_URL, headers=headers, timeout=15)
    resp.raise_for_status()


class MetroNorthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MTA Metro North."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY) or None
            selected_stations = user_input.get(CONF_STATIONS, [])
            update_interval = user_input.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

            if not selected_stations:
                errors[CONF_STATIONS] = "no_stations"
            else:
                try:
                    await self.hass.async_add_executor_job(_validate_connection, api_key)
                except requests.HTTPError as err:
                    if err.response is not None and err.response.status_code == 403:
                        errors[CONF_API_KEY] = "invalid_api_key"
                    else:
                        errors["base"] = "cannot_connect"
                except requests.RequestException:
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title="MTA Metro North",
                        data={
                            CONF_API_KEY: api_key,
                            CONF_STATIONS: selected_stations,
                            CONF_UPDATE_INTERVAL: update_interval,
                        },
                    )

        station_options = {v: v for v in HARLEM_LINE_STATIONS.values()}

        schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY, default=""): str,
                vol.Required(CONF_STATIONS): vol.In(station_options),
                vol.Optional(
                    CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "stations": ", ".join(HARLEM_LINE_STATIONS.values())
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options for Metro North."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.data
        station_options = {v: v for v in HARLEM_LINE_STATIONS.values()}

        schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY, default=current.get(CONF_API_KEY, "")): str,
                vol.Required(
                    CONF_STATIONS, default=current.get(CONF_STATIONS, [])
                ): vol.In(station_options),
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_UPDATE_INTERVAL, max=MAX_UPDATE_INTERVAL),
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
