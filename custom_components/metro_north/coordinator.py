"""Data update coordinator for MTA Metro North."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
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
from .gtfs_static import GTFSStaticManager

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


class PeakWindow:
    """A time window with its own poll interval."""

    def __init__(self, start: str, end: str, interval: int) -> None:
        self.start = _parse_time(start)
        self.end = _parse_time(end)
        self.interval = interval

    def is_active(self, now: time) -> bool:
        if self.start <= self.end:
            return self.start <= now <= self.end
        # Crosses midnight
        return now >= self.start or now <= self.end


def _parse_time(s: str) -> time:
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]))


class MetroNorthCoordinator(DataUpdateCoordinator):
    """Coordinator — fetches GTFS-RT and adjusts poll rate by time of day."""

    def __init__(
        self,
        hass: HomeAssistant,
        gtfs_static: GTFSStaticManager,
        default_interval: int,
        peak_windows: list[PeakWindow],
    ) -> None:
        self._gtfs = gtfs_static
        self._headers: dict[str, str] = {}
        self._default_interval = default_interval
        self._peak_windows = peak_windows

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._get_current_interval()),
        )

    def _get_current_interval(self) -> int:
        """Return the interval (seconds) appropriate for the current time."""
        now = datetime.now().time()
        for window in self._peak_windows:
            if window.is_active(now):
                return window.interval
        return self._default_interval

    async def _async_refresh(self) -> None:
        """Update the poll interval before re-scheduling."""
        new_interval = timedelta(seconds=self._get_current_interval())
        if new_interval != self.update_interval:
            _LOGGER.debug(
                "Metro North poll interval → %s s", new_interval.total_seconds()
            )
            self.update_interval = new_interval
        await super()._async_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        # Keep static GTFS fresh in the background (non-blocking on failure)
        try:
            await self._gtfs.async_ensure_loaded()
        except Exception as err:
            _LOGGER.warning("Static GTFS refresh failed (non-fatal): %s", err)

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

    # ── GTFS-RT trip updates ───────────────────────────────────────────────

    def _fetch_trip_updates(self) -> dict[str, list[dict[str, Any]]]:
        response = requests.get(
            GTFS_RT_URL, headers=self._headers, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        # Build a complete stop_id set — GTFS stops if loaded, else fallback
        known_stops: set[str]
        if self._gtfs.is_loaded():
            known_stops = set(self._gtfs.get_all_stops().keys())
        else:
            known_stops = set(HARLEM_LINE_STATIONS.keys())

        # stop_id → list[train dict]
        stops: dict[str, list[dict[str, Any]]] = {sid: [] for sid in known_stops}

        now_ts = datetime.now(timezone.utc).timestamp()

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            tu = entity.trip_update
            trip_id = tu.trip.trip_id
            route_id = tu.trip.route_id
            direction = tu.trip.direction_id

            # Build trip stop list from static GTFS for this trip
            static_stops = self._gtfs.get_trip_stops(trip_id) if self._gtfs.is_loaded() else []

            for stu in tu.stop_time_update:
                stop_id = stu.stop_id
                if stop_id not in stops:
                    continue

                scheduled_ts: int | None = None
                delay_seconds = 0

                if stu.HasField("departure"):
                    scheduled_ts = stu.departure.time or None
                    delay_seconds = stu.departure.delay or 0
                elif stu.HasField("arrival"):
                    scheduled_ts = stu.arrival.time or None
                    delay_seconds = stu.arrival.delay or 0

                if scheduled_ts is None:
                    continue

                estimated_ts = scheduled_ts + delay_seconds
                if estimated_ts < now_ts - 60:
                    continue  # already departed

                # Best-effort track number from vehicle label
                track = ""
                if tu.HasField("vehicle"):
                    track = tu.vehicle.label or ""

                delay_minutes = round(delay_seconds / 60)

                # Upcoming stops from static schedule (stops after this one)
                upcoming_stops = self._build_upcoming_stops(static_stops, stu.stop_sequence)

                stops[stop_id].append(
                    {
                        "trip_id": trip_id,
                        "route_id": route_id,
                        "route_name": self._gtfs.get_route_name(route_id)
                        if self._gtfs.is_loaded()
                        else route_id,
                        "direction": direction,
                        "headsign": self._get_headsign(trip_id),
                        "scheduled_time": datetime.fromtimestamp(
                            scheduled_ts, tz=timezone.utc
                        ).isoformat(),
                        "estimated_time": datetime.fromtimestamp(
                            estimated_ts, tz=timezone.utc
                        ).isoformat(),
                        "delay_minutes": delay_minutes,
                        "track": track,
                        "stop_sequence": stu.stop_sequence,
                        "destination": self._resolve_terminus(tu, static_stops, last=True),
                        "origin": self._resolve_terminus(tu, static_stops, last=False),
                        "trip_stops": upcoming_stops,
                    }
                )

        for stop_id in stops:
            stops[stop_id].sort(key=lambda x: x["estimated_time"])

        return stops

    def _get_headsign(self, trip_id: str) -> str:
        if not self._gtfs.is_loaded():
            return ""
        info = self._gtfs.get_trip_info(trip_id)
        return info.headsign if info else ""

    @staticmethod
    def _build_upcoming_stops(
        static_stops: list, current_sequence: int
    ) -> list[dict[str, Any]]:
        """All static stops at or after the current stop sequence."""
        result = []
        for s in static_stops:
            if s.stop_sequence >= current_sequence:
                result.append(
                    {
                        "stop_sequence": s.stop_sequence,
                        "stop_id": s.stop_id,
                        "stop_name": s.stop_name,
                        "arrival_time": s.arrival_time,
                        "departure_time": s.departure_time,
                    }
                )
        return result

    def _resolve_terminus(
        self,
        trip_update: Any,
        static_stops: list,
        last: bool,
    ) -> str:
        """Resolve origin or destination from static stops, then fallback to RT data."""
        if static_stops:
            stop = static_stops[-1] if last else static_stops[0]
            return stop.stop_name

        rt_stops = list(trip_update.stop_time_update)
        if not rt_stops:
            return "Unknown"
        stop_id = rt_stops[-1].stop_id if last else rt_stops[0].stop_id
        if self._gtfs.is_loaded():
            return self._gtfs.get_stop_name(stop_id)
        return HARLEM_LINE_STATIONS.get(stop_id, stop_id)

    # ── GTFS-RT vehicle positions ──────────────────────────────────────────

    def _fetch_vehicles(self) -> list[dict[str, Any]]:
        try:
            response = requests.get(
                GTFS_RT_VEHICLES_URL, headers=self._headers, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
        except requests.HTTPError as err:
            _LOGGER.debug("Vehicle positions feed returned %s, skipping", err)
            return []
        except requests.RequestException as err:
            _LOGGER.debug("Vehicle positions unavailable: %s", err)
            return []

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        vehicles = []
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            vp = entity.vehicle

            try:
                lat = vp.position.latitude
                lon = vp.position.longitude
            except Exception:
                continue

            if lat == 0.0 and lon == 0.0:
                continue

            current_stop_id = vp.stop_id if vp.stop_id else ""
            current_stop_name = (
                self._gtfs.get_stop_name(current_stop_id)
                if self._gtfs.is_loaded()
                else HARLEM_LINE_STATIONS.get(current_stop_id, current_stop_id)
            )

            trip_id = vp.trip.trip_id if vp.HasField("trip") else ""
            route_id = vp.trip.route_id if vp.HasField("trip") else ""

            vehicles.append(
                {
                    "vehicle_id": vp.vehicle.id or entity.id,
                    "label": vp.vehicle.label or vp.vehicle.id or entity.id,
                    "trip_id": trip_id,
                    "route_id": route_id,
                    "route_name": self._gtfs.get_route_name(route_id)
                    if self._gtfs.is_loaded()
                    else route_id,
                    "headsign": self._get_headsign(trip_id),
                    "latitude": lat,
                    "longitude": lon,
                    "bearing": vp.position.bearing,
                    "speed": round(vp.position.speed * 2.237, 1) if vp.position.speed else 0,
                    "current_stop_id": current_stop_id,
                    "current_stop_name": current_stop_name,
                    "current_stop_sequence": vp.current_stop_sequence,
                    "timestamp": vp.timestamp,
                    "trip_stops": self._gtfs.get_trip_stops(trip_id)
                    if self._gtfs.is_loaded()
                    else [],
                }
            )

        return vehicles
