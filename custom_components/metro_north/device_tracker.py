"""Device tracker platform for MTA Metro North train vehicles."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SOURCE_TYPE_GPS
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BEARING,
    ATTR_LINE,
    ATTR_OCCUPANCY,
    ATTR_SPEED,
    ATTR_VEHICLE_ID,
    DOMAIN,
    HARLEM_LINE_STATIONS,
)
from .coordinator import MetroNorthCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MetroNorthCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.async_add_listener(
        lambda: _check_new_vehicles(hass, coordinator, async_add_entities, entry)
    )
    _check_new_vehicles(hass, coordinator, async_add_entities, entry)


_tracked_vehicles: dict[str, set[str]] = {}


def _check_new_vehicles(
    hass: HomeAssistant,
    coordinator: MetroNorthCoordinator,
    async_add_entities: AddEntitiesCallback,
    entry: ConfigEntry,
) -> None:
    if coordinator.data is None:
        return

    entry_key = entry.entry_id
    if entry_key not in _tracked_vehicles:
        _tracked_vehicles[entry_key] = set()

    vehicles = coordinator.data.get("vehicles", [])
    new_entities = []
    for vehicle in vehicles:
        vid = vehicle.get("vehicle_id")
        if vid and vid not in _tracked_vehicles[entry_key]:
            _tracked_vehicles[entry_key].add(vid)
            new_entities.append(TrainVehicleTracker(coordinator, vid))

    if new_entities:
        async_add_entities(new_entities)


class TrainVehicleTracker(CoordinatorEntity[MetroNorthCoordinator], TrackerEntity):
    """Represents a Metro North train vehicle on the map."""

    def __init__(self, coordinator: MetroNorthCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{DOMAIN}_vehicle_{vehicle_id}"
        self._attr_icon = "mdi:train"

    @property
    def name(self) -> str:
        vehicle = self._get_vehicle()
        label = vehicle.get("label", self._vehicle_id) if vehicle else self._vehicle_id
        return f"Metro North Train {label}"

    def _get_vehicle(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        for v in self.coordinator.data.get("vehicles", []):
            if v.get("vehicle_id") == self._vehicle_id:
                return v
        return None

    @property
    def latitude(self) -> float | None:
        v = self._get_vehicle()
        return v.get("latitude") if v else None

    @property
    def longitude(self) -> float | None:
        v = self._get_vehicle()
        return v.get("longitude") if v else None

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_GPS

    @property
    def device_info(self) -> dict[str, Any]:
        v = self._get_vehicle()
        label = v.get("label", self._vehicle_id) if v else self._vehicle_id
        return {
            "identifiers": {(DOMAIN, f"vehicle_{self._vehicle_id}")},
            "name": f"Metro North Train {label}",
            "manufacturer": "MTA Metro North",
            "model": "Harlem Line Train",
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self._get_vehicle()
        if not v:
            return {}
        current_stop = HARLEM_LINE_STATIONS.get(v.get("current_stop_id", ""), "")
        return {
            ATTR_VEHICLE_ID: v.get("vehicle_id"),
            "label": v.get("label"),
            "trip_id": v.get("trip_id"),
            "route_id": v.get("route_id"),
            ATTR_BEARING: v.get("bearing"),
            ATTR_SPEED: v.get("speed"),
            "current_stop": current_stop,
            ATTR_LINE: "Harlem",
        }
