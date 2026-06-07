"""Sensor platform for MTA Metro North."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CURRENT_STOP,
    ATTR_DELAY_MINUTES,
    ATTR_DEPARTURE_STATUS,
    ATTR_DESTINATION,
    ATTR_DIRECTION,
    ATTR_ESTIMATED_TIME,
    ATTR_HEADSIGN,
    ATTR_LINE,
    ATTR_ORIGIN,
    ATTR_SCHEDULED_TIME,
    ATTR_SERVICE_ALERTS,
    ATTR_SERVICE_TYPE,
    ATTR_STOPS_REMAINING,
    ATTR_TRACK,
    ATTR_TRAIN_NUMBER,
    ATTR_TRIP_STOPS,
    ATTR_UPCOMING_TRAINS,
    CONF_DIRECTION,
    CONF_LOCAL_STOP_INDICATORS,
    CONF_NUM_TRAINS,
    CONF_ROUTES,
    CONF_STATIONS,
    DEFAULT_NUM_TRAINS,
    DIRECTION_BOTH,
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    DOMAIN,
    STATION_NAME_TO_ID,
)
from .coordinator import MetroNorthCoordinator

_LOGGER = logging.getLogger(__name__)

MAX_UPCOMING = 10
MAX_TRIP_STOPS = 50  # cap attribute payload size


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MetroNorthCoordinator = hass.data[DOMAIN][entry.entry_id]
    config = {**entry.data, **entry.options}

    selected = config.get(CONF_STATIONS, [])
    if isinstance(selected, str):
        selected = [selected]

    direction = config.get(CONF_DIRECTION, DIRECTION_BOTH)
    if direction not in (DIRECTION_BOTH, DIRECTION_INBOUND, DIRECTION_OUTBOUND):
        direction = DIRECTION_BOTH

    num_trains = max(1, min(20, int(config.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS))))

    raw_indicators = config.get(CONF_LOCAL_STOP_INDICATORS, "")
    local_indicators: set[str] = (
        {s.strip() for s in raw_indicators.split(",") if s.strip()}
        if raw_indicators else set()
    )

    routes: list[str] = config.get(CONF_ROUTES, [])
    if isinstance(routes, str):
        routes = [routes] if routes else []

    entities: list[SensorEntity] = []
    expected_unique_ids: set[str] = set()

    for station_name in selected:
        stop_id = _resolve_stop_id(coordinator, station_name)
        if stop_id is None:
            _LOGGER.warning("Cannot resolve stop ID for station: %s", station_name)
            continue
        for position in range(1, num_trains + 1):
            uid = f"{DOMAIN}_train_{position}_{stop_id}"
            expected_unique_ids.add(uid)
            entities.append(
                TrainAtPositionSensor(coordinator, stop_id, station_name, position, direction, local_indicators, routes)
            )
            dep_uid = f"{DOMAIN}_departure_status_{position}_{stop_id}"
            expected_unique_ids.add(dep_uid)
            entities.append(
                TrainDepartureStatusSensor(coordinator, stop_id, station_name, position, direction, routes)
            )
        expected_unique_ids.add(f"{DOMAIN}_upcoming_{stop_id}")
        expected_unique_ids.add(f"{DOMAIN}_alerts_{stop_id}")
        entities.append(UpcomingTrainsSensor(coordinator, stop_id, station_name, direction, routes))
        entities.append(ServiceAlertSensor(coordinator, stop_id, station_name))

    # Remove stale entities that no longer match the current config
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id not in expected_unique_ids:
            registry.async_remove(entity_entry.entity_id)

    async_add_entities(entities)


def _resolve_stop_id(coordinator: MetroNorthCoordinator, name: str) -> str | None:
    """Look up stop_id from GTFS static data first, then fallback map."""
    gtfs = coordinator._gtfs
    if gtfs.is_loaded():
        sid = gtfs.stop_id_for_name(name)
        if sid:
            return sid
    return STATION_NAME_TO_ID.get(name)


def _filter_by_routes(trains: list[dict], routes: list[str]) -> list[dict]:
    """Keep only trains whose route_name contains one of the configured route fragments."""
    if not routes:
        return trains
    return [
        t for t in trains
        if any(r.lower() in t.get("route_name", "").lower() for r in routes)
    ]


def _filtered_trains(trains: list[dict], direction: str) -> list[dict]:
    """Filter by GTFS direction_id: Metro North 0 = Inbound (Grand Central), 1 = Outbound."""
    if direction == DIRECTION_INBOUND:
        return [t for t in trains if t.get("direction") == 0]
    if direction == DIRECTION_OUTBOUND:
        return [t for t in trains if t.get("direction") == 1]
    return trains


def _direction_suffix(direction: str) -> str:
    if direction == DIRECTION_INBOUND:
        return " Inbound"
    if direction == DIRECTION_OUTBOUND:
        return " Outbound"
    return ""


def _train_attrs_summary(t: dict[str, Any]) -> dict[str, Any]:
    """Compact attribute dict for the upcoming-trains list — omits trip_stops."""
    return {
        ATTR_TRAIN_NUMBER: t.get("train_number") or t.get("trip_id", ""),
        ATTR_TRACK: t.get("track", ""),
        ATTR_SCHEDULED_TIME: _fmt_time(t.get("scheduled_time")),
        ATTR_ESTIMATED_TIME: _fmt_time(t.get("estimated_time")),
        ATTR_DELAY_MINUTES: t.get("delay_minutes", 0),
        "status": t.get("status", ""),
        ATTR_DEPARTURE_STATUS: t.get("departure_status", ""),
        ATTR_ORIGIN: t.get("origin", ""),
        ATTR_DESTINATION: t.get("destination", ""),
        ATTR_HEADSIGN: t.get("headsign") or t.get("destination", ""),
        ATTR_LINE: t.get("route_name", "Metro North"),
        ATTR_DIRECTION: "Inbound" if t.get("direction") == 0 else "Outbound",
    }


def _train_attrs(t: dict[str, Any]) -> dict[str, Any]:
    """Build the common attribute dict for one train."""
    stops_raw = t.get("trip_stops", [])[:MAX_TRIP_STOPS]
    trip_stops = [
        {
            "stop_name": s.get("stop_name", ""),
            "arrival": s.get("arrival", ""),
            "departure": s.get("departure", ""),
        }
        for s in stops_raw
        if isinstance(s, dict)
    ]
    return {
        ATTR_TRAIN_NUMBER: t.get("train_number") or t.get("trip_id", ""),
        ATTR_TRACK: t.get("track", ""),
        ATTR_SCHEDULED_TIME: _fmt_time(t.get("scheduled_time")),
        ATTR_ESTIMATED_TIME: _fmt_time(t.get("estimated_time")),
        ATTR_DELAY_MINUTES: t.get("delay_minutes", 0),
        "status": t.get("status", ""),
        ATTR_DEPARTURE_STATUS: t.get("departure_status", ""),
        ATTR_ORIGIN: t.get("origin", ""),
        ATTR_DESTINATION: t.get("destination", ""),
        ATTR_HEADSIGN: t.get("headsign") or t.get("destination", ""),
        ATTR_LINE: t.get("route_name", "Metro North"),
        ATTR_DIRECTION: "Inbound" if t.get("direction") == 0 else "Outbound",
        ATTR_TRIP_STOPS: trip_stops,
    }


class _StationBase(CoordinatorEntity[MetroNorthCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._station_name = station_name

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, f"station_{self._stop_id}")},
            "name": f"Metro North {self._station_name}",
            "manufacturer": "MTA Metro North",
            "model": "Station",
        }

    def _get_trains(self) -> list[dict[str, Any]]:
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("trip_updates", {}).get(self._stop_id, [])


class TrainAtPositionSensor(_StationBase, SensorEntity):
    """The Nth upcoming train (after direction filter) at a station."""

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
        position: int,
        direction: str,
        local_indicators: set[str] | None = None,
        routes: list[str] | None = None,
    ) -> None:
        super().__init__(coordinator, stop_id, station_name)
        self._position = position
        self._direction = direction
        self._local_indicators: set[str] = local_indicators or set()
        self._routes: list[str] = routes or []
        suffix = _direction_suffix(direction)
        self._attr_unique_id = f"{DOMAIN}_train_{position}_{stop_id}"
        self._attr_name = f"Train {position}{suffix}"
        self._attr_icon = "mdi:train"

    def _get_target(self) -> dict[str, Any] | None:
        trains = _filtered_trains(self._get_trains(), self._direction)
        trains = _filter_by_routes(trains, self._routes)
        if len(trains) >= self._position:
            return trains[self._position - 1]
        return None

    def _resolve_service_type(self, t: dict[str, Any]) -> str:
        """Override heuristic with user-configured local indicator stops."""
        if not self._local_indicators:
            return t.get("service_type", "")
        all_stops = set(t.get("all_stop_names", []))
        return "Local" if all_stops & self._local_indicators else "Express"

    @property
    def native_value(self) -> str | None:
        t = self._get_target()
        return _fmt_time(t.get("estimated_time")) if t else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._get_target()
        if not t:
            return {}
        attrs = _train_attrs(t)
        attrs[ATTR_SERVICE_TYPE] = self._resolve_service_type(t)
        attrs[ATTR_CURRENT_STOP] = t.get("current_stop", "")
        attrs[ATTR_STOPS_REMAINING] = t.get("stops_remaining", 0)
        # lat/lon from current stop so HA can show this sensor on the map
        if t.get("latitude") is not None:
            attrs["latitude"] = t["latitude"]
        if t.get("longitude") is not None:
            attrs["longitude"] = t["longitude"]
        attrs["diagnostic"] = t.get("_diagnostic", {})
        return attrs


class UpcomingTrainsSensor(_StationBase, SensorEntity):
    """Count + full list of upcoming trains at a station."""

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
        direction: str,
        routes: list[str] | None = None,
    ) -> None:
        super().__init__(coordinator, stop_id, station_name)
        self._direction = direction
        self._routes: list[str] = routes or []
        suffix = _direction_suffix(direction)
        self._attr_unique_id = f"{DOMAIN}_upcoming_{stop_id}"
        self._attr_name = f"Upcoming Trains{suffix}"
        self._attr_icon = "mdi:train-variant"

    def _get_filtered(self) -> list[dict]:
        trains = _filtered_trains(self._get_trains(), self._direction)
        return _filter_by_routes(trains, self._routes)[:MAX_UPCOMING]

    @property
    def native_value(self) -> int:
        return len(self._get_filtered())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_UPCOMING_TRAINS: [_train_attrs_summary(t) for t in self._get_filtered()]}


class TrainDepartureStatusSensor(_StationBase, SensorEntity):
    """Departure status for the Nth upcoming train at a station."""

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
        position: int,
        direction: str,
        routes: list[str] | None = None,
    ) -> None:
        super().__init__(coordinator, stop_id, station_name)
        self._position = position
        self._direction = direction
        self._routes: list[str] = routes or []
        suffix = _direction_suffix(direction)
        self._attr_unique_id = f"{DOMAIN}_departure_status_{position}_{stop_id}"
        self._attr_name = f"Train {position}{suffix} Departure Status"
        self._attr_icon = "mdi:train-car-flatbed-tank"

    def _get_target(self) -> dict[str, Any] | None:
        trains = _filtered_trains(self._get_trains(), self._direction)
        trains = _filter_by_routes(trains, self._routes)
        if len(trains) >= self._position:
            return trains[self._position - 1]
        return None

    @property
    def native_value(self) -> str | None:
        t = self._get_target()
        return t.get("departure_status", "") if t else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._get_target()
        if not t:
            return {}
        return {
            ATTR_TRAIN_NUMBER: t.get("train_number") or t.get("trip_id", ""),
            ATTR_SCHEDULED_TIME: _fmt_time(t.get("scheduled_time")),
            ATTR_ESTIMATED_TIME: _fmt_time(t.get("estimated_time")),
            ATTR_DELAY_MINUTES: t.get("delay_minutes", 0),
            ATTR_CURRENT_STOP: t.get("current_stop", ""),
        }


class ServiceAlertSensor(_StationBase, SensorEntity):
    """Active service alert count + details for a station."""

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
    ) -> None:
        super().__init__(coordinator, stop_id, station_name)
        self._attr_unique_id = f"{DOMAIN}_alerts_{stop_id}"
        self._attr_name = "Service Alerts"
        self._attr_icon = "mdi:alert-circle-outline"

    def _get_alerts(self) -> list[dict]:
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("service_alerts", {}).get(self._stop_id, [])

    @property
    def native_value(self) -> int:
        return len(self._get_alerts())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_SERVICE_ALERTS: self._get_alerts()}


def _fmt_time(iso: str | None) -> str | None:
    if iso is None:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%-I:%M %p")
    except (ValueError, OSError):
        return iso
