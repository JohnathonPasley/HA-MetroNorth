"""Config flow for MTA Metro North integration."""
from __future__ import annotations

import logging
from typing import Any

import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_DEFAULT_INTERVAL,
    CONF_PEAK_1_END,
    CONF_PEAK_1_INTERVAL,
    CONF_PEAK_1_START,
    CONF_PEAK_2_END,
    CONF_PEAK_2_INTERVAL,
    CONF_PEAK_2_START,
    CONF_STATIONS,
    DEFAULT_OFF_PEAK_INTERVAL,
    DEFAULT_PEAK_1_END,
    DEFAULT_PEAK_1_START,
    DEFAULT_PEAK_2_END,
    DEFAULT_PEAK_2_START,
    DEFAULT_PEAK_INTERVAL,
    DOMAIN,
    GTFS_RT_URL,
    HARLEM_LINE_STATIONS,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from .gtfs_static import GTFSStaticManager

_LOGGER = logging.getLogger(__name__)


def _test_connection(api_key: str | None) -> None:
    headers = {"x-api-key": api_key} if api_key else {}
    resp = requests.get(GTFS_RT_URL, headers=headers, timeout=15)
    resp.raise_for_status()


def _build_station_options(gtfs: GTFSStaticManager) -> list[selector.SelectOptionDict]:
    """Return station names as SelectOptionDict list, sorted alphabetically."""
    if gtfs.is_loaded():
        names = gtfs.sorted_stop_names()
    else:
        names = sorted(HARLEM_LINE_STATIONS.values())
    return [{"value": n, "label": n} for n in names]


class MetroNorthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MTA Metro North."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._gtfs: GTFSStaticManager | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        # Lazy-init GTFS manager and try to pre-load station list
        if self._gtfs is None:
            self._gtfs = GTFSStaticManager(self.hass)
            try:
                await self._gtfs.async_ensure_loaded()
            except Exception:
                pass  # non-fatal — fallback list used

        if user_input is not None:
            api_key = user_input.get(CONF_API_KEY) or None
            selected = user_input.get(CONF_STATIONS, [])
            if isinstance(selected, str):
                selected = [selected]

            if not selected:
                errors[CONF_STATIONS] = "no_stations"
            else:
                try:
                    await self.hass.async_add_executor_job(_test_connection, api_key)
                except requests.HTTPError as err:
                    if err.response is not None and err.response.status_code == 403:
                        errors[CONF_API_KEY] = "invalid_api_key"
                    else:
                        errors["base"] = "cannot_connect"
                except requests.RequestException:
                    errors["base"] = "cannot_connect"
                else:
                    self._api_key = api_key
                    # Proceed to schedule step with stations stored
                    return await self.async_step_schedule(
                        pre_filled={CONF_STATIONS: selected, CONF_API_KEY: api_key}
                    )

        station_options = _build_station_options(self._gtfs)

        schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_STATIONS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=station_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_schedule(
        self,
        user_input: dict[str, Any] | None = None,
        pre_filled: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Step 2: configure polling intervals and peak windows."""
        if pre_filled is not None:
            # Store carry-over data from step 1 and show the form
            self._carry = pre_filled
            return self.async_show_form(
                step_id="schedule",
                data_schema=_schedule_schema(),
            )

        if user_input is not None:
            data = {**self._carry, **user_input}
            return self.async_create_entry(title="MTA Metro North", data=data)

        return self.async_show_form(
            step_id="schedule",
            data_schema=_schedule_schema(),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Allow editing all settings after initial setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._gtfs: GTFSStaticManager | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._gtfs is None:
            self._gtfs = GTFSStaticManager(self.hass)
            try:
                await self._gtfs.async_ensure_loaded()
            except Exception:
                pass

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        station_options = _build_station_options(self._gtfs)
        current_stations = current.get(CONF_STATIONS, [])
        if isinstance(current_stations, str):
            current_stations = [current_stations]

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_API_KEY, default=current.get(CONF_API_KEY, "")
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_STATIONS, default=current_stations
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=station_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                **_schedule_fields(current),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


def _schedule_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(_schedule_fields(d))


def _schedule_fields(d: dict[str, Any]) -> dict:
    return {
        vol.Optional(
            CONF_PEAK_1_START, default=d.get(CONF_PEAK_1_START, DEFAULT_PEAK_1_START)
        ): selector.TimeSelector(),
        vol.Optional(
            CONF_PEAK_1_END, default=d.get(CONF_PEAK_1_END, DEFAULT_PEAK_1_END)
        ): selector.TimeSelector(),
        vol.Optional(
            CONF_PEAK_1_INTERVAL, default=d.get(CONF_PEAK_1_INTERVAL, DEFAULT_PEAK_INTERVAL)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_INTERVAL, max=MAX_INTERVAL, step=5, unit_of_measurement="s"
            )
        ),
        vol.Optional(
            CONF_PEAK_2_START, default=d.get(CONF_PEAK_2_START, DEFAULT_PEAK_2_START)
        ): selector.TimeSelector(),
        vol.Optional(
            CONF_PEAK_2_END, default=d.get(CONF_PEAK_2_END, DEFAULT_PEAK_2_END)
        ): selector.TimeSelector(),
        vol.Optional(
            CONF_PEAK_2_INTERVAL, default=d.get(CONF_PEAK_2_INTERVAL, DEFAULT_PEAK_INTERVAL)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_INTERVAL, max=MAX_INTERVAL, step=5, unit_of_measurement="s"
            )
        ),
        vol.Optional(
            CONF_DEFAULT_INTERVAL,
            default=d.get(CONF_DEFAULT_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL),
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_INTERVAL, max=MAX_INTERVAL, step=10, unit_of_measurement="s"
            )
        ),
    }
