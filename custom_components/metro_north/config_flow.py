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
    CONF_DEFAULT_INTERVAL,
    CONF_DIRECTION,
    CONF_NUM_TRAINS,
    CONF_PEAK_1_END,
    CONF_PEAK_1_INTERVAL,
    CONF_PEAK_1_START,
    CONF_PEAK_2_END,
    CONF_PEAK_2_INTERVAL,
    CONF_PEAK_2_START,
    CONF_STATIONS,
    DEFAULT_NUM_TRAINS,
    DEFAULT_OFF_PEAK_INTERVAL,
    DEFAULT_PEAK_1_END,
    DEFAULT_PEAK_1_START,
    DEFAULT_PEAK_2_END,
    DEFAULT_PEAK_2_START,
    DEFAULT_PEAK_INTERVAL,
    DIRECTION_BOTH,
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    DOMAIN,
    FALLBACK_STATIONS,
    GTFS_RT_URL,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from .gtfs_static import GTFSStaticManager

_LOGGER = logging.getLogger(__name__)


def _test_connection() -> None:
    resp = requests.get(GTFS_RT_URL, timeout=15)
    resp.raise_for_status()


_DIRECTION_OPTIONS = [
    {"value": DIRECTION_BOTH, "label": "Both directions"},
    {"value": DIRECTION_INBOUND, "label": "Inbound only (toward Grand Central)"},
    {"value": DIRECTION_OUTBOUND, "label": "Outbound only (from Grand Central)"},
]


def _build_station_options(gtfs: GTFSStaticManager) -> list[selector.SelectOptionDict]:
    if gtfs.is_loaded():
        names = gtfs.sorted_stop_names()
    else:
        names = sorted(FALLBACK_STATIONS.values())
    return [{"value": n, "label": n} for n in names]


class MetroNorthConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MTA Metro North."""

    VERSION = 1

    def __init__(self) -> None:
        self._carry: dict[str, Any] = {}
        self._gtfs: GTFSStaticManager | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: pick stations (station list loaded from GTFS)."""
        errors: dict[str, str] = {}

        if self._gtfs is None:
            self._gtfs = GTFSStaticManager(self.hass)
            try:
                await self._gtfs.async_ensure_loaded()
            except Exception:
                pass  # non-fatal — fallback list used

        if user_input is not None:
            selected = user_input.get(CONF_STATIONS, [])
            if isinstance(selected, str):
                selected = [selected]

            if not selected:
                errors[CONF_STATIONS] = "no_stations"
            else:
                try:
                    await self.hass.async_add_executor_job(_test_connection)
                except requests.RequestException:
                    errors["base"] = "cannot_connect"
                else:
                    self._carry = {
                        CONF_STATIONS: selected,
                        CONF_DIRECTION: user_input.get(CONF_DIRECTION, DIRECTION_BOTH),
                        CONF_NUM_TRAINS: int(user_input.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS)),
                    }
                    return await self.async_step_schedule()

        station_options = _build_station_options(self._gtfs)

        schema = vol.Schema(
            {
                vol.Optional(CONF_STATIONS, default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=station_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_DIRECTION, default=DIRECTION_BOTH): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_DIRECTION_OPTIONS,
                        multiple=False,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_NUM_TRAINS, default=DEFAULT_NUM_TRAINS): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=5, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_schedule(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: configure polling intervals and peak windows."""
        if user_input is not None:
            return self.async_create_entry(
                title="MTA Metro North",
                data={**self._carry, **user_input},
            )

        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(_schedule_fields({})),
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
                    CONF_STATIONS, default=current_stations
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=station_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_DIRECTION, default=current.get(CONF_DIRECTION, DIRECTION_BOTH)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_DIRECTION_OPTIONS,
                        multiple=False,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_NUM_TRAINS, default=int(current.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=5, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                **_schedule_fields(current),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


def _schedule_fields(d: dict[str, Any]) -> dict:
    return {
        vol.Optional(
            CONF_PEAK_1_START, default=d.get(CONF_PEAK_1_START, DEFAULT_PEAK_1_START)
        ): selector.TimeSelector(),
        vol.Optional(
            CONF_PEAK_1_END, default=d.get(CONF_PEAK_1_END, DEFAULT_PEAK_1_END)
        ): selector.TimeSelector(),
        vol.Optional(
            CONF_PEAK_1_INTERVAL,
            default=d.get(CONF_PEAK_1_INTERVAL, DEFAULT_PEAK_INTERVAL),
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
            CONF_PEAK_2_INTERVAL,
            default=d.get(CONF_PEAK_2_INTERVAL, DEFAULT_PEAK_INTERVAL),
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
