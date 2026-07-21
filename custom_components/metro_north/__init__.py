"""MTA Metro North Home Assistant Integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_DEFAULT_INTERVAL,
    CONF_HISTORY_DAYS,
    CONF_PEAK_1_DAYS,
    CONF_PEAK_1_END,
    CONF_PEAK_1_INTERVAL,
    CONF_PEAK_1_START,
    CONF_PEAK_2_DAYS,
    CONF_PEAK_2_END,
    CONF_PEAK_2_INTERVAL,
    CONF_PEAK_2_START,
    DEFAULT_HISTORY_DAYS,
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

PLATFORMS = ["sensor"]
_GTFS_MANAGER_KEY = "_gtfs_manager"
_PURGE_UNSUB_KEY = "_purge_unsub"


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


async def _async_purge_recorder(hass: HomeAssistant, entry: ConfigEntry, keep_days: int) -> None:
    """Purge Metro North entity history older than keep_days via recorder.purge_entities."""
    if not hass.services.has_service("recorder", "purge_entities"):
        _LOGGER.debug("recorder.purge_entities unavailable; skipping purge")
        return
    entity_reg = er.async_get(hass)
    entity_ids = [
        e.entity_id
        for e in er.async_entries_for_config_entry(entity_reg, entry.entry_id)
    ]
    if not entity_ids:
        return
    try:
        await hass.services.async_call(
            "recorder",
            "purge_entities",
            {"entity_id": entity_ids, "keep_days": keep_days},
            blocking=True,
        )
        _LOGGER.info(
            "Purged Metro North recorder history older than %d days (%d entities)",
            keep_days, len(entity_ids),
        )
    except Exception as err:
        _LOGGER.debug("Recorder purge non-fatal error: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # ── One-time migration cleanup ─────────────────────────────────────────
    # Remove stale vehicle device_tracker entities and their HA devices left
    # over from the now-deleted device_tracker.py (introduced before v1.15.4).
    entity_reg = er.async_get(hass)
    stale_entities = [
        e for e in er.async_entries_for_config_entry(entity_reg, entry.entry_id)
        if e.domain == "device_tracker"
    ]
    for stale in stale_entities:
        entity_reg.async_remove(stale.entity_id)

    device_reg = dr.async_get(hass)
    stale_devices = [
        d for d in dr.async_entries_for_config_entry(device_reg, entry.entry_id)
        if any("vehicle_" in str(ident) for _, ident in d.identifiers)
    ]
    for device in stale_devices:
        device_reg.async_remove_device(device.id)

    if stale_entities or stale_devices:
        _LOGGER.info(
            "Cleaned up %d stale device_tracker entities and %d vehicle devices",
            len(stale_entities), len(stale_devices),
        )

    # ── Coordinator setup ──────────────────────────────────────────────────
    config = {**entry.data, **entry.options}

    # Reuse existing GTFSStaticManager across reloads to avoid re-downloading on every options change
    if _GTFS_MANAGER_KEY not in hass.data[DOMAIN]:
        hass.data[DOMAIN][_GTFS_MANAGER_KEY] = GTFSStaticManager(hass)
    gtfs_static = hass.data[DOMAIN][_GTFS_MANAGER_KEY]

    coordinator = MetroNorthCoordinator(
        hass=hass,
        gtfs_static=gtfs_static,
        default_interval=int(config.get(CONF_DEFAULT_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL)),
        peak_windows=_build_peak_windows(config),
    )

    # First refresh — also triggers GTFS static download
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Start the self-managing poll loop (sleeps between peak windows automatically)
    coordinator.start_polling()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # ── Recorder retention ─────────────────────────────────────────────────
    keep_days = int(config.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))

    async def _purge_callback(_now: Any = None) -> None:
        """Run recorder purge — safe to use as event-loop callback or task."""
        await _async_purge_recorder(hass, entry, keep_days)

    if hass.is_running:
        hass.async_create_task(_purge_callback())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _purge_callback)

    # async_track_time_interval invokes the async callback directly on the
    # event loop; no async_create_task wrapper needed (that pattern would
    # cross a thread boundary and trip HA's thread-safety guard).
    unsub_purge = async_track_time_interval(
        hass,
        _purge_callback,
        timedelta(hours=24),
    )
    hass.data[DOMAIN].setdefault(_PURGE_UNSUB_KEY, {})[entry.entry_id] = unsub_purge

    # ── Services ───────────────────────────────────────────────────────────
    if not hass.services.has_service(DOMAIN, "purge_history"):
        async def _handle_purge(call) -> None:  # type: ignore[type-arg]
            days = int(call.data.get("keep_days", DEFAULT_HISTORY_DAYS))
            for eid in list(hass.data.get(DOMAIN, {}).keys()):
                coord = hass.data[DOMAIN].get(eid)
                if isinstance(coord, MetroNorthCoordinator):
                    cfg_entry = hass.config_entries.async_get_entry(eid)
                    if cfg_entry:
                        await _async_purge_recorder(hass, cfg_entry, days)

        hass.services.async_register(
            DOMAIN, "purge_history", _handle_purge,
            schema=vol.Schema({vol.Optional("keep_days", default=DEFAULT_HISTORY_DAYS): vol.Coerce(int)}),
        )

    if not hass.services.has_service(DOMAIN, "update_gtfs"):
        async def _handle_update_gtfs(call) -> None:  # type: ignore[type-arg]
            """Force-download fresh GTFS static data and reload all entries."""
            for eid, coord in list(hass.data.get(DOMAIN, {}).items()):
                if isinstance(coord, MetroNorthCoordinator):
                    await coord._gtfs.async_force_refresh()
                    await hass.config_entries.async_reload(eid)

        hass.services.async_register(DOMAIN, "update_gtfs", _handle_update_gtfs)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if isinstance(coordinator, MetroNorthCoordinator):
        coordinator.stop_polling()
    unsub = hass.data[DOMAIN].get(_PURGE_UNSUB_KEY, {}).pop(entry.entry_id, None)
    if unsub:
        unsub()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Nothing extra to clean up when the integration is fully removed."""


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
