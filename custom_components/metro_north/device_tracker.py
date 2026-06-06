"""Device tracker platform — train vehicles on the HA map."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BEARING,
    ATTR_HEADSIGN,
    ATTR_LINE,
    ATTR_SPEED,
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MetroNorthCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Per-entry set stored in hass.data so it is cleaned up on unload.
    seen: set[str] = set()
    hass.data[DOMAIN].setdefault(_TRACKED_KEY, {})[entry.entry_id] = seen

    def _on_update() -> None:
        if coordinator.data is None:
            return
        new_entities = []
        for vehicle in coordinator.data.get("vehicles", []):
            vid = vehicle.get("vehicle_id")
            if vid and vid not in seen:
                seen.add(vid)
                new_entities.append(TrainVehicleTracker(coordinator, vid))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_on_update))
    entry.async_on_unload(
        lambda: hass.data[DOMAIN].get(_TRACKED_KEY, {}).pop(entry.entry_id, None)
    )
    _on_update()


class TrainVehicleTracker(CoordinatorEntity[MetroNorthCoordinator], TrackerEntity):
    """A Metro North train vehicle — appears as a pin on the HA map."""

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
            "label": v.get("label"),
            "trip_id": v.get("trip_id"),
            ATTR_LINE: v.get("route_name", "Metro North"),
            ATTR_HEADSIGN: v.get("headsign", ""),
            ATTR_BEARING: v.get("bearing"),
            ATTR_SPEED: f"{v.get('speed', 0)} mph",
            "current_stop": v.get("current_stop_name", ""),
            "current_stop_sequence": v.get("current_stop_sequence"),
            ATTR_TRIP_STOPS: trip_stops,
        }
