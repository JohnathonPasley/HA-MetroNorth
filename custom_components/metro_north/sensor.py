"""Sensor platform for MTA Metro North."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BEARING,
    ATTR_DELAY_MINUTES,
    ATTR_DESTINATION,
    ATTR_DIRECTION,
    ATTR_ESTIMATED_TIME,
    ATTR_HEADSIGN,
    ATTR_LINE,
    ATTR_ORIGIN,
    ATTR_SCHEDULED_TIME,
    ATTR_TRACK,
    ATTR_TRAIN_NUMBER,
    ATTR_TRIP_STOPS,
    ATTR_UPCOMING_TRAINS,
    CONF_DIRECTION,
    CONF_NUM_TRAINS,
    CONF_STATIONS,
    DEFAULT_NUM_TRAINS,
    DIRECTION_BOTH,
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    DOMAIN,
    FALLBACK_STATIONS,
    STATION_NAME_TO_ID,
)
from .coordinator import MetroNorthCoordinator

_LOGGER = logging.getLogger(__name__)


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
    num_trains = int(config.get(CONF_NUM_TRAINS, DEFAULT_NUM_TRAINS))

    entities: list[SensorEntity] = []
    for station_name in selected:
        stop_id = _resolve_stop_id(coordinator, station_name)
        if stop_id is None:
            _LOGGER.warning("Cannot resolve stop ID for station: %s", station_name)
            continue

        # One sensor per train slot (1 … num_trains)
        for pos in range(1, num_trains + 1):
            entities.append(
                TrainAtPositionSensor(coordinator, stop_id, station_name, pos, direction)
            )
        # One combined upcoming-list sensor
        entities.append(UpcomingTrainsSensor(coordinator, stop_id, station_name, direction))

    async_add_entities(entities)


def _resolve_stop_id(coordinator: MetroNorthCoordinator, name: str) -> str | None:
    gtfs = coordinator._gtfs
    if gtfs.is_loaded():
        sid = gtfs.stop_id_for_name(name)
        if sid:
            return sid
    return STATION_NAME_TO_ID.get(name)


# ── Base ─────────────────────────────────────────────────────────────────────

class _StationBase(CoordinatorEntity[MetroNorthCoordinator]):
    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
        direction: str,
    ) -> None:
        super().__init__(coordinator)
        self._stop_id = stop_id
        self._station_name = station_name
        self._direction = direction

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, f"station_{self._stop_id}")},
            "name": f"Metro North – {self._station_name}",
            "manufacturer": "MTA Metro North",
            "model": "Station",
        }

    def _all_trains(self) -> list[dict[str, Any]]:
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("trip_updates", {}).get(self._stop_id, [])

    def _filtered_trains(self) -> list[dict[str, Any]]:
        trains = self._all_trains()
        if self._direction == DIRECTION_INBOUND:
            return [t for t in trains if t.get("direction") == 0]
        if self._direction == DIRECTION_OUTBOUND:
            return [t for t in trains if t.get("direction") == 1]
        return trains


# ── Individual train sensor (position 1 = next, 2 = second, etc.) ────────────

class TrainAtPositionSensor(_StationBase, SensorEntity):
    """Shows the Nth upcoming train for a station."""

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
        position: int,
        direction: str,
    ) -> None:
        super().__init__(coordinator, stop_id, station_name, direction)
        self._position = position  # 1-based
        suffix = _direction_suffix(direction)
        uid_dir = direction if direction != DIRECTION_BOTH else "all"
        self._attr_unique_id = f"{DOMAIN}_train_{uid_dir}_{position}_{stop_id}"
        self._attr_name = f"Metro North {station_name}{suffix} Train {position}"
        self._attr_icon = "mdi:train"

    def _get_train(self) -> dict[str, Any] | None:
        trains = self._filtered_trains()
        if len(trains) < self._position:
            return None
        return trains[self._position - 1]

    @property
    def native_value(self) -> str | None:
        t = self._get_train()
        return _fmt_time(t.get("estimated_time")) if t else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._get_train()
        if not t:
            return {"status": "No Train"}
        return _train_attrs(t)


# ── Upcoming list sensor ───────────────────────────────────────────────────

class UpcomingTrainsSensor(_StationBase, SensorEntity):
    """Exposes a list of all upcoming trains for a station."""

    def __init__(
        self,
        coordinator: MetroNorthCoordinator,
        stop_id: str,
        station_name: str,
        direction: str,
    ) -> None:
        super().__init__(coordinator, stop_id, station_name, direction)
        suffix = _direction_suffix(direction)
        uid_dir = direction if direction != DIRECTION_BOTH else "all"
        self._attr_unique_id = f"{DOMAIN}_upcoming_{uid_dir}_{stop_id}"
        self._attr_name = f"Metro North {station_name}{suffix} Upcoming Trains"
        self._attr_icon = "mdi:train-variant"

    @property
    def native_value(self) -> int:
        return len(self._filtered_trains())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        upcoming = [_train_attrs(t) for t in self._filtered_trains()]
        return {ATTR_UPCOMING_TRAINS: upcoming}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _direction_suffix(direction: str) -> str:
    if direction == DIRECTION_INBOUND:
        return " Inbound"
    if direction == DIRECTION_OUTBOUND:
        return " Outbound"
    return ""


def _train_attrs(t: dict[str, Any]) -> dict[str, Any]:
    delay = t.get("delay_minutes", 0)
    direction_id = t.get("direction", -1)
    direction_label = (
        "Inbound (to Grand Central)"
        if direction_id == 0
        else "Outbound (from Grand Central)"
        if direction_id == 1
        else "Unknown"
    )
    stops = t.get("trip_stops", [])
    return {
        ATTR_TRAIN_NUMBER: t.get("trip_id", ""),
        ATTR_TRACK: t.get("track", ""),
        ATTR_SCHEDULED_TIME: _fmt_time(t.get("scheduled_time")),
        ATTR_ESTIMATED_TIME: _fmt_time(t.get("estimated_time")),
        ATTR_DELAY_MINUTES: delay,
        "status": t.get("status", ""),
        ATTR_DESTINATION: t.get("destination", ""),
        ATTR_ORIGIN: t.get("origin", ""),
        ATTR_HEADSIGN: t.get("headsign", ""),
        ATTR_LINE: t.get("route_name", "Metro North"),
        ATTR_DIRECTION: direction_label,
        ATTR_TRIP_STOPS: [
            {
                "stop": s.get("stop_name", ""),
                "arrival": s.get("arrival", ""),
                "departure": s.get("departure", ""),
            }
            for s in stops
        ],
    }


def _fmt_time(iso: str | None) -> str | None:
    if iso is None:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%-I:%M %p")
    except (ValueError, OSError):
        return iso
