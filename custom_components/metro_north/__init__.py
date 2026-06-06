"""MTA Metro North Home Assistant Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util.slugify import slugify

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
_STATION_ZONES_KEY = "_station_zones"


async def _async_create_station_zones(hass: HomeAssistant, gtfs_static) -> set[str]:
    """Create HA zones for all GTFS stops. Returns set of created entity_ids."""
    if not hass.services.has_service("zone", "create"):
        _LOGGER.warning("zone.create service not available; station zones skipped")
        return set()

    created: set[str] = set()
    stops = gtfs_static.get_all_stops()

    for stop_id, stop_info in stops.items():
        if stop_info.lat == 0.0 and stop_info.lon == 0.0:
            continue

        zone_name = f"MNR {stop_info.name}"
        entity_id = f"zone.{slugify(zone_name)}"

        if hass.states.get(entity_id) is not None:
            created.add(entity_id)
            continue

        try:
            await hass.services.async_call(
                "zone",
                "create",
                {
                    "name": zone_name,
                    "latitude": float(stop_info.lat),
                    "longitude": float(stop_info.lon),
                    "radius": 100,
                    "icon": "mdi:train-station",
                    "passive": True,
                },
                blocking=True,
            )
            created.add(entity_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not create zone for %s: %s", stop_info.name, err)

    _LOGGER.info("Created/verified %d Metro North station zones", len(created))
    return created


async def _async_remove_station_zones(hass: HomeAssistant, entity_ids: set[str]) -> None:
    """Delete station zones created by this integration."""
    if not hass.services.has_service("zone", "delete"):
        return

    for entity_id in entity_ids:
        if hass.states.get(entity_id) is not None:
            try:
                await hass.services.async_call(
                    "zone",
                    "delete",
                    {"entity_id": entity_id},
                    blocking=True,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Could not delete zone %s: %s", entity_id, err)


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

    # Register manual GTFS refresh service (once; safe to call on every entry load)
    if not hass.services.has_service(DOMAIN, "update_gtfs"):
        async def _handle_update_gtfs(call) -> None:  # type: ignore[type-arg]
            """Force-download fresh GTFS static data, recreate zones, reload all entries."""
            for eid, coord in list(hass.data.get(DOMAIN, {}).items()):
                if isinstance(coord, MetroNorthCoordinator):
                    await coord._gtfs.async_force_refresh()
                    # Refresh station zones with updated GTFS data
                    if coord._gtfs.is_loaded():
                        existing = hass.data[DOMAIN].get(_STATION_ZONES_KEY, set())
                        await _async_remove_station_zones(hass, existing)
                        hass.data[DOMAIN][_STATION_ZONES_KEY] = set()
                        new_zones = await _async_create_station_zones(hass, coord._gtfs)
                        hass.data[DOMAIN][_STATION_ZONES_KEY] = new_zones
                    await hass.config_entries.async_reload(eid)

        hass.services.async_register(DOMAIN, "update_gtfs", _handle_update_gtfs)

    # Create station zones once (shared across all config entries)
    if _STATION_ZONES_KEY not in hass.data[DOMAIN]:
        hass.data[DOMAIN][_STATION_ZONES_KEY] = set()
        if coordinator._gtfs.is_loaded():
            zone_ids = await _async_create_station_zones(hass, coordinator._gtfs)
            hass.data[DOMAIN][_STATION_ZONES_KEY] = zone_ids

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up station zones when the integration is fully removed."""
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if remaining:
        return
    zone_ids = hass.data.get(DOMAIN, {}).pop(_STATION_ZONES_KEY, set())
    if zone_ids:
        await _async_remove_station_zones(hass, zone_ids)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
