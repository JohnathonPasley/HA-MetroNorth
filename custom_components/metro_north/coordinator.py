"""Data update coordinator for MTA Metro North."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from google.transit import gtfs_realtime_pb2

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    GTFS_RT_URL,
    GTFS_RT_VEHICLES_URL,
    HARLEM_LINE_STATIONS,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


class MetroNorthCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches GTFS-RT data for Metro North."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str | None,
        update_interval: int,
    ) -> None:
        self.api_key = api_key
        self._headers: dict[str, str] = {}
        if api_key:
            self._headers["x-api-key"] = api_key

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            trip_data = await self.hass.async_add_executor_job(self._fetch_trip_updates)
            vehicle_data = await self.hass.async_add_executor_job(self._fetch_vehicles)
        except requests.RequestException as err:
            raise UpdateFailed(f"Error fetching Metro North data: {err}") from err

        return {
            "trip_updates": trip_data,
            "vehicles": vehicle_data,
            "last_updated": datetime.now(timezone.utc),
        }

    def _fetch_trip_updates(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch and parse GTFS-RT trip updates feed."""
        response = requests.get(
            GTFS_RT_URL, headers=self._headers, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        # stop_id -> list of upcoming departures
        stops: dict[str, list[dict[str, Any]]] = {k: [] for k in HARLEM_LINE_STATIONS}

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            tu = entity.trip_update
            trip_id = tu.trip.trip_id
            route_id = tu.trip.route_id
            direction = tu.trip.direction_id  # 0 = inbound (to GCT), 1 = outbound

            for stu in tu.stop_time_update:
                stop_id = stu.stop_id
                if stop_id not in HARLEM_LINE_STATIONS:
                    continue

                scheduled_ts = None
                estimated_ts = None

                if stu.departure.time:
                    scheduled_ts = stu.departure.time
                    estimated_ts = scheduled_ts + stu.departure.delay
                elif stu.arrival.time:
                    scheduled_ts = stu.arrival.time
                    estimated_ts = scheduled_ts + (stu.arrival.delay or 0)

                if scheduled_ts is None:
                    continue

                now_ts = datetime.now(timezone.utc).timestamp()
                if estimated_ts < now_ts - 60:
                    continue  # already departed

                delay_seconds = (stu.departure.delay or stu.arrival.delay or 0)
                delay_minutes = round(delay_seconds / 60)

                track = ""
                if tu.HasField("vehicle"):
                    track = tu.vehicle.label or ""

                # Check MTA NYCT extension for track info if available
                try:
                    mnr_ext = tu.Extensions[
                        gtfs_realtime_pb2.MnrTripDescriptor.mnr_trip_descriptor
                    ]
                    track = getattr(mnr_ext, "track", track)
                except Exception:
                    pass

                stops[stop_id].append(
                    {
                        "trip_id": trip_id,
                        "route_id": route_id,
                        "direction": direction,
                        "scheduled_time": datetime.fromtimestamp(
                            scheduled_ts, tz=timezone.utc
                        ).isoformat(),
                        "estimated_time": datetime.fromtimestamp(
                            estimated_ts, tz=timezone.utc
                        ).isoformat(),
                        "delay_minutes": delay_minutes,
                        "track": track,
                        "stop_sequence": stu.stop_sequence,
                        "destination": self._resolve_destination(tu, stu.stop_sequence),
                        "origin": self._resolve_origin(tu, stu.stop_sequence),
                    }
                )

        # Sort each stop's trains by estimated departure time
        for stop_id in stops:
            stops[stop_id].sort(key=lambda x: x["estimated_time"])

        return stops

    def _fetch_vehicles(self) -> list[dict[str, Any]]:
        """Fetch and parse GTFS-RT vehicle positions feed."""
        try:
            response = requests.get(
                GTFS_RT_VEHICLES_URL, headers=self._headers, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
        except requests.HTTPError:
            # Vehicle feed may not always be available; non-fatal
            _LOGGER.debug("Vehicle positions feed unavailable, skipping")
            return []

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        vehicles = []
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp = entity.vehicle
            if not vp.position.HasField or not (vp.position.latitude and vp.position.longitude):
                try:
                    lat = vp.position.latitude
                    lon = vp.position.longitude
                except Exception:
                    continue

            lat = vp.position.latitude
            lon = vp.position.longitude
            if lat == 0.0 and lon == 0.0:
                continue

            vehicles.append(
                {
                    "vehicle_id": vp.vehicle.id or entity.id,
                    "label": vp.vehicle.label or vp.vehicle.id or entity.id,
                    "trip_id": vp.trip.trip_id,
                    "route_id": vp.trip.route_id,
                    "latitude": lat,
                    "longitude": lon,
                    "bearing": vp.position.bearing,
                    "speed": vp.position.speed,
                    "current_stop_id": vp.stop_id,
                    "current_stop_sequence": vp.current_stop_sequence,
                    "occupancy": vp.occupancy_status if vp.HasField("occupancy_status") else None,
                    "timestamp": vp.timestamp,
                }
            )

        return vehicles

    @staticmethod
    def _resolve_destination(trip_update: Any, current_sequence: int) -> str:
        """Best-effort destination from the last stop in trip update."""
        stops = list(trip_update.stop_time_update)
        if not stops:
            return "Unknown"
        last_stop_id = stops[-1].stop_id
        return HARLEM_LINE_STATIONS.get(last_stop_id, last_stop_id)

    @staticmethod
    def _resolve_origin(trip_update: Any, current_sequence: int) -> str:
        """Best-effort origin from the first stop in trip update."""
        stops = list(trip_update.stop_time_update)
        if not stops:
            return "Unknown"
        first_stop_id = stops[0].stop_id
        return HARLEM_LINE_STATIONS.get(first_stop_id, first_stop_id)
