"""Sensor platform for MTA Metro North."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
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
    CONF_STATIONS,
    DOMAIN,
    HARLEM_LINE_STATIONS,
    STATION_NAME_TO_ID,
    TRAIN_STATUS_DELAYED,
    TRAIN_STATUS_ON_TIME,
)
from .coordinator import MetroNorthCoordinator

_LOGGER = logging.getLogger(__name__)

MAX_UPCOMING = 10


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MetroNorthCoordinator = hass.data[DOMAIN][entry.entry_id]
    selected = entry.data.get(CONF_STATIONS, [])
    if isinstance(selected, str):
        selected = [selected]

    entities: list[SensorEntity] = []
    for station_name in selected:
        stop_id = _resolve_stop_id(coordinator, station_name)
        if stop_id is None:
            _LOGGER.warning("Cannot resolve stop ID for station: %s", station_name)
            continue
        entities.append(NextTrainSensor(coordinator, stop_id, station_name))
        entities.append(UpcomingTrainsSensor(coordinator, stop_id, station_name))

    async_add_entities(entities)


def _resolve_stop_id(coordinator: MetroNorthCoordinator, name: str) -> str | None:
    """Look up stop_id from GTFS static data first, then fallback map."""
    gtfs = coordinator._gtfs
    if gtfs.is_loaded():
        sid = gtfs.stop_id_for_name(name)
        if sid:
            return sid
    return STATION_NAME_TO_ID.get(name)


class _StationBase(CoordinatorEntity[MetroNorthCoordinator]):
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
            "name": f"Metro North – {self._station_name}",
            "manufacturer": "MTA Metro North",
            "model": "Station",
        }

    def _get_trains(self) -> list[dict[str, Any]]:
        if self.coordinator.data is None:
            return []
        return self.coordinator.data.get("trip_updates", {}).get(self._stop_id, [])


class NextTrainSensor(_StationBase, SensorEntity):
    """Next departing train from a station."""

    def __init__(self, coordinator: MetroNorthCoordinator, stop_id: str, station_name: str) -> None:
        super().__init__(coordinator, stop_id, station_name)
        self._attr_unique_id = f"{DOMAIN}_next_train_{stop_id}"
        self._attr_name = f"Metro North {station_name} Next Train"
        self._attr_icon = "mdi:train"

    @property
    def native_value(self) -> str | None:
        trains = self._get_trains()
        if not trains:
            return None
        return _fmt_time(trains[0].get("estimated_time"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        trains = self._get_trains()
        if not trains:
            return {}
        t = trains[0]
        delay = t.get("delay_minutes", 0)
        stops = t.get("trip_stops", [])
        return {
            ATTR_TRAIN_NUMBER: t.get("trip_id", ""),
            ATTR_TRACK: t.get("track", ""),
            ATTR_SCHEDULED_TIME: _fmt_time(t.get("scheduled_time")),
            ATTR_ESTIMATED_TIME: _fmt_time(t.get("estimated_time")),
            ATTR_DELAY_MINUTES: delay,
            "status": TRAIN_STATUS_DELAYED if delay > 1 else TRAIN_STATUS_ON_TIME,
            ATTR_DESTINATION: t.get("destination", ""),
            ATTR_ORIGIN: t.get("origin", ""),
            ATTR_HEADSIGN: t.get("headsign", ""),
            ATTR_LINE: t.get("route_name", "Metro North"),
            ATTR_DIRECTION: "Inbound" if t.get("direction") == 0 else "Outbound",
            ATTR_TRIP_STOPS: [
                {
                    "stop_name": s["stop_name"],
                    "arrival": s["arrival_time"],
                    "departure": s["departure_time"],
                }
                for s in stops
            ],
        }


class UpcomingTrainsSensor(_StationBase, SensorEntity):
    """Next N trains from a station with full stop lists."""

    def __init__(self, coordinator: MetroNorthCoordinator, stop_id: str, station_name: str) -> None:
        super().__init__(coordinator, stop_id, station_name)
        self._attr_unique_id = f"{DOMAIN}_upcoming_{stop_id}"
        self._attr_name = f"Metro North {station_name} Upcoming Trains"
        self._attr_icon = "mdi:train-variant"

    @property
    def native_value(self) -> int:
        return len(self._get_trains()[:MAX_UPCOMING])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        trains = self._get_trains()[:MAX_UPCOMING]
        upcoming = []
        for t in trains:
            delay = t.get("delay_minutes", 0)
            stops = t.get("trip_stops", [])
            upcoming.append(
                {
                    ATTR_TRAIN_NUMBER: t.get("trip_id", ""),
                    ATTR_TRACK: t.get("track", ""),
                    ATTR_SCHEDULED_TIME: _fmt_time(t.get("scheduled_time")),
                    ATTR_ESTIMATED_TIME: _fmt_time(t.get("estimated_time")),
                    ATTR_DELAY_MINUTES: delay,
                    "status": TRAIN_STATUS_DELAYED if delay > 1 else TRAIN_STATUS_ON_TIME,
                    ATTR_DESTINATION: t.get("destination", ""),
                    ATTR_HEADSIGN: t.get("headsign", ""),
                    ATTR_DIRECTION: "Inbound" if t.get("direction") == 0 else "Outbound",
                    ATTR_LINE: t.get("route_name", "Metro North"),
                    ATTR_TRIP_STOPS: [
                        {
                            "stop_name": s["stop_name"],
                            "arrival": s["arrival_time"],
                            "departure": s["departure_time"],
                        }
                        for s in stops
                    ],
                }
            )
        return {ATTR_UPCOMING_TRAINS: upcoming}


def _fmt_time(iso: str | None) -> str | None:
    if iso is None:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().strftime("%-I:%M %p")
    except (ValueError, OSError):
        return iso
