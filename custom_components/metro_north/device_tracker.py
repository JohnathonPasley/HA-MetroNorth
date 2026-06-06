"""Device tracker platform — train vehicles on the HA map."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BEARING,
    ATTR_HEADSIGN,
    ATTR_LINE,
    ATTR_SPEED,
    ATTR_TRAIN_NUMBER,
    ATTR_TRIP_STOPS,
    ATTR_VEHICLE_ID,
    DOMAIN,
)
from .coordinator import MetroNorthCoordinator

_LOGGER = logging.getLogger(__name__)
_TRACKED_KEY = "_tracked_vehicles"

MAX_LABEL_LEN = 32
MAX_HEADSIGN_LEN = 64
MAX_TRIP_STOPS = 50

# Colored SVG circle pins per line (URL-encoded data URIs)
_LINE_PICTURES: dict[str, str] = {
    "harlem": (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Ccircle cx='12' cy='12' r='10' fill='%231565C0'/%3E%3C/svg%3E"
    ),
    "hudson": (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Ccircle cx='12' cy='12' r='10' fill='%232E7D32'/%3E%3C/svg%3E"
    ),
    "new_haven": (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Ccircle cx='12' cy='12' r='10' fill='%23C62828'/%3E%3C/svg%3E"
    ),
    "unknown": (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
        "%3Ccircle cx='12' cy='12' r='10' fill='%23757575'/%3E%3C/svg%3E"
    ),
}

# Gray square "M" pin for station markers
_STATION_PICTURE = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Crect x='4' y='4' width='16' height='16' rx='3' fill='%23424242'/%3E"
    "%3Cpath d='M8 16V8h2l2 4 2-4h2v8h-2v-4l-2 4-2-4v4z' fill='white'/%3E%3C/svg%3E"
)


def _line_key(route_name: str) -> str:
    rn = (route_name or "").lower()
    if "harlem" in rn:
        return "harlem"
    if "hudson" in rn:
        return "hudson"
    if "haven" in rn or "new haven" in rn:
        return "new_haven"
    return "unknown"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MetroNorthCoordinator = hass.data[DOMAIN][entry.entry_id]

    # ── Station markers (static, from GTFS stops.txt) ────────────────────
    if coordinator._gtfs.is_loaded():
        station_entities = [
            StationMarkerTracker(stop_id, stop_info)
            for stop_id, stop_info in coordinator._gtfs.get_all_stops().items()
            if stop_info.lat != 0.0 or stop_info.lon != 0.0
        ]
        if station_entities:
            async_add_entities(station_entities)
            _LOGGER.debug("Added %d station markers to map", len(station_entities))

    seen: set[str] = set()
    hass.data[DOMAIN].setdefault(_TRACKED_KEY, {})[entry.entry_id] = seen

    def _on_update() -> None:
        if coordinator.data is None:
            return

        current_ids = {
            v["vehicle_id"]
            for v in coordinator.data.get("vehicles", [])
            if v.get("vehicle_id")
        }

        # Add newly seen vehicles
        new_entities = []
        for vehicle in coordinator.data.get("vehicles", []):
            vid = vehicle.get("vehicle_id")
            if vid and vid not in seen:
                seen.add(vid)
                new_entities.append(TrainVehicleTracker(coordinator, vid))
        if new_entities:
            async_add_entities(new_entities)

        # Remove vehicles that have left the feed
        stale = seen - current_ids
        if stale:
            registry = er.async_get(hass)
            for vid in stale:
                entity_id = registry.async_get_entity_id(
                    "device_tracker", DOMAIN, f"{DOMAIN}_vehicle_{vid}"
                )
                if entity_id:
                    registry.async_remove(entity_id)
            seen.difference_update(stale)

    entry.async_on_unload(coordinator.async_add_listener(_on_update))
    entry.async_on_unload(
        lambda: hass.data[DOMAIN].get(_TRACKED_KEY, {}).pop(entry.entry_id, None)
    )
    _on_update()


class TrainVehicleTracker(CoordinatorEntity[MetroNorthCoordinator], TrackerEntity):
    """A Metro North train vehicle — color-coded pin on the HA map."""

    def __init__(self, coordinator: MetroNorthCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{DOMAIN}_vehicle_{vehicle_id}"
        self._attr_icon = "mdi:train"

    def _get_vehicle(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        for v in self.coordinator.data.get("vehicles", []):
            if v.get("vehicle_id") == self._vehicle_id:
                return v
        return None

    @property
    def name(self) -> str:
        v = self._get_vehicle()
        label = (str(v.get("label") or self._vehicle_id))[:MAX_LABEL_LEN] if v else self._vehicle_id
        headsign = (str(v.get("headsign") or ""))[:MAX_HEADSIGN_LEN] if v else ""
        if headsign:
            return f"Train {label} → {headsign}"
        return f"Metro North Train {label}"

    @property
    def entity_picture(self) -> str:
        """Return a colored circle SVG based on the train's line."""
        v = self._get_vehicle()
        route_name = v.get("route_name", "") if v else ""
        return _LINE_PICTURES[_line_key(route_name)]

    @property
    def latitude(self) -> float | None:
        v = self._get_vehicle()
        return float(v["latitude"]) if v and v.get("latitude") is not None else None

    @property
    def longitude(self) -> float | None:
        v = self._get_vehicle()
        return float(v["longitude"]) if v and v.get("longitude") is not None else None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def device_info(self) -> dict[str, Any]:
        v = self._get_vehicle()
        label = (str(v.get("label") or self._vehicle_id))[:MAX_LABEL_LEN] if v else self._vehicle_id
        return {
            "identifiers": {(DOMAIN, f"vehicle_{self._vehicle_id}")},
            "name": f"Metro North Train {label}",
            "manufacturer": "MTA Metro North",
            "model": "Train Vehicle",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self._get_vehicle()
        if not v:
            return {}

        raw_stops = v.get("trip_stops", [])[:MAX_TRIP_STOPS]
        trip_stops = []
        for s in raw_stops:
            if hasattr(s, "stop_name"):
                trip_stops.append(
                    {
                        "stop_sequence": s.stop_sequence,
                        "stop_name": s.stop_name,
                        "arrival": s.arrival_time,
                        "departure": s.departure_time,
                    }
                )
            elif isinstance(s, dict):
                trip_stops.append(s)

        return {
            ATTR_VEHICLE_ID: v.get("vehicle_id"),
            ATTR_TRAIN_NUMBER: v.get("train_number") or v.get("label") or v.get("trip_id"),
            "trip_id": v.get("trip_id"),
            ATTR_LINE: v.get("route_name", "Metro North"),
            ATTR_HEADSIGN: v.get("headsign", ""),
            ATTR_BEARING: v.get("bearing"),
            ATTR_SPEED: f"{v.get('speed', 0)} mph",
            "current_stop": v.get("current_stop_name", ""),
            "current_stop_sequence": v.get("current_stop_sequence"),
            ATTR_TRIP_STOPS: trip_stops,
        }


class StationMarkerTracker(TrackerEntity):
    """Fixed map pin for a Metro North station, sourced from GTFS stops.txt."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.GPS
    _attr_icon = "mdi:train-station"
    _attr_should_poll = False

    def __init__(self, stop_id: str, stop_info: Any) -> None:
        self._stop_id = stop_id
        self._stop_info = stop_info
        self._attr_unique_id = f"{DOMAIN}_station_{stop_id}"
        self._attr_name = stop_info.name

    @property
    def latitude(self) -> float | None:
        return self._stop_info.lat if self._stop_info.lat != 0.0 else None

    @property
    def longitude(self) -> float | None:
        return self._stop_info.lon if self._stop_info.lon != 0.0 else None

    @property
    def entity_picture(self) -> str:
        return _STATION_PICTURE

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "metro_north_network")},
            "name": "Metro North Network",
            "manufacturer": "MTA Metro North",
            "model": "Station Network",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"stop_id": self._stop_id}
        if self._stop_info.platform_code:
            attrs["platform_code"] = self._stop_info.platform_code
        return attrs
