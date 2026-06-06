"""MTA Metro North Home Assistant Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEFAULT_INTERVAL,
    CONF_PEAK_1_DAYS,
    CONF_PEAK_1_END,
    CONF_PEAK_1_INTERVAL,
    CONF_PEAK_1_START,
    CONF_PEAK_2_DAYS,
    CONF_PEAK_2_END,
    CONF_PEAK_2_INTERVAL,
    CONF_PEAK_2_START,
    DEFAULT_OFF_PEAK_INTERVAL,
    DEFAULT_PEAK_1_END,
    DEFAULT_PEAK_1_START,
    DEFAULT_PEAK_2_END,
    DEFAULT_PEAK_2_START,
    DEFAULT_PEAK_DAYS,
    DEFAULT_PEAK_INTERVAL,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from .coordinator import MetroNorthCoordinator, PeakWindow
from .gtfs_static import GTFSStaticManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "device_tracker"]


def _build_peak_windows(data: dict) -> list[PeakWindow]:
    windows = []
    for start_key, end_key, interval_key, days_key, def_start, def_end in [
        (CONF_PEAK_1_START, CONF_PEAK_1_END, CONF_PEAK_1_INTERVAL, CONF_PEAK_1_DAYS, DEFAULT_PEAK_1_START, DEFAULT_PEAK_1_END),
        (CONF_PEAK_2_START, CONF_PEAK_2_END, CONF_PEAK_2_INTERVAL, CONF_PEAK_2_DAYS, DEFAULT_PEAK_2_START, DEFAULT_PEAK_2_END),
    ]:
        start = data.get(start_key, def_start)
        end = data.get(end_key, def_end)
        interval = max(MIN_INTERVAL, min(MAX_INTERVAL, int(data.get(interval_key, DEFAULT_PEAK_INTERVAL))))
        days_raw = data.get(days_key, DEFAULT_PEAK_DAYS)
        days = {int(d) for d in days_raw} if days_raw else set()
        if start and end:
            windows.append(PeakWindow(start=start, end=end, interval=interval, days=days))
    return windows


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Merge entry.data + entry.options (options override on re-configure)
    config = {**entry.data, **entry.options}

    gtfs_static = GTFSStaticManager(hass)

    coordinator = MetroNorthCoordinator(
        hass=hass,
        gtfs_static=gtfs_static,
        default_interval=int(config.get(CONF_DEFAULT_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL)),
        peak_windows=_build_peak_windows(config),
    )

    # First refresh — also triggers GTFS static download
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
