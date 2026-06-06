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
    FALLBACK_STATIONS,
    GTFS_RT_URL,
    GTFS_RT_VEHICLES_URL,
)
from .gtfs_static import GTFSStaticManager
from .mta_extensions import extract_stop_time_update_ext
from .mta_mercury import extract_mercury_alert, extract_mercury_entity_selector, get_translated_text

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
_MAX_TRACK_LEN = 16
_MAX_STATUS_LEN = 64

# GTFS-RT Alert cause/effect enum → display string
_ALERT_CAUSE = {
    1: "Unknown Cause",
    2: "Other Cause",
    3: "Technical Problem",
    4: "Strike",
    5: "Demonstration",
    6: "Accident",
    7: "Holiday",
    8: "Weather",
    9: "Maintenance",
    10: "Construction",
    11: "Police Activity",
    12: "Medical Emergency",
}
_ALERT_EFFECT = {
    1: "No Service",
    2: "Reduced Service",
    3: "Significant Delays",
    4: "Detour",
    5: "Additional Service",
    6: "Modified Service",
    7: "Other Effect",
    8: "Unknown Effect",
    9: "Stop Moved",
    10: "No Effect",
    11: "Accessibility Issue",
}


def _alert_enum_name(val: int, mapping: dict) -> str:
    return mapping.get(val, "")


def _sanitize(s: str, max_len: int) -> str:
    """Strip non-printable characters and truncate to max_len."""
    return "".join(c for c in s if c.isprintable())[:max_len]


class PeakWindow:
    def __init__(self, start: str, end: str, interval: int) -> None:
        self.start = _parse_time(start)
        self.end = _parse_time(end)
        self.interval = interval

    def is_active(self, now: time) -> bool:
        if self.start <= self.end:
            return self.start <= now <= self.end
        return now >= self.start or now <= self.end


def _parse_time(s: str) -> time:
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]))


def _train_status(delay_minutes: int) -> str:
    if delay_minutes > 1:
        return f"Delayed {delay_minutes} min"
    if delay_minutes < -1:
        return f"Early {abs(delay_minutes)} min"
    return "On Time"


class MetroNorthCoordinator(DataUpdateCoordinator):
    """Coordinator — fetches GTFS-RT, parses trips + vehicles, adjusts poll rate."""

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
        now = datetime.now().time()
        for window in self._peak_windows:
            if window.is_active(now):
                return window.interval
        return self._default_interval

    async def _async_refresh(self, **kwargs) -> None:
        new_interval = timedelta(seconds=self._get_current_interval())
        if new_interval != self.update_interval:
            _LOGGER.debug("Metro North poll interval → %s s", new_interval.total_seconds())
            self.update_interval = new_interval
        await super()._async_refresh(**kwargs)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._gtfs.async_ensure_loaded()
        except Exception as err:
            _LOGGER.warning("Static GTFS refresh failed (non-fatal): %s", err)

        try:
            trip_data, vehicle_data, alert_data = await self.hass.async_add_executor_job(
                self._fetch_all_rt
            )
        except requests.RequestException as err:
            raise UpdateFailed(f"Error fetching Metro North data: {err}") from err

        return {
            "trip_updates": trip_data,
            "vehicles": vehicle_data,
            "service_alerts": alert_data,
            "last_updated": datetime.now(timezone.utc),
        }

    # ── Combined GTFS-RT fetch ─────────────────────────────────────────────

    def _fetch_all_rt(self) -> tuple[dict[str, list], list[dict], dict[str, list]]:
        """Fetch main feed; parse trip updates, vehicle positions, and service alerts.
        Then supplement with the dedicated vehicle feed if available."""
        response = requests.get(GTFS_RT_URL, headers=self._headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(response.content)
        except Exception as err:
            raise UpdateFailed(f"Main GTFS-RT feed returned non-protobuf data: {err}") from err

        known_stops: set[str] = (
            set(self._gtfs.get_all_stops().keys())
            if self._gtfs.is_loaded()
            else set(FALLBACK_STATIONS.keys())
        )
        stops: dict[str, list] = {sid: [] for sid in known_stops}
        vehicles: dict[str, dict] = {}  # vehicle_id → vehicle dict (dedup)
        alerts: dict[str, list] = {}   # stop_id or route_id → [alert_dict, ...]

        now_ts = datetime.now(timezone.utc).timestamp()

        for entity in feed.entity:
            if entity.HasField("trip_update"):
                self._parse_trip_update(entity.trip_update, stops, now_ts)
            if entity.HasField("vehicle"):
                v = self._parse_vehicle_entity(entity.vehicle, entity.id)
                if v:
                    vehicles[v["vehicle_id"]] = v
            if entity.HasField("alert"):
                self._parse_alert(entity.alert, alerts, now_ts)

        # Sort each stop by estimated departure
        for sid in stops:
            stops[sid].sort(key=lambda x: x["estimated_time"])

        # Supplement with dedicated vehicle feed (may fail silently)
        self._supplement_vehicles(vehicles)

        # If the feed has no real VehiclePosition data (the vehicles endpoint is
        # frequently unavailable for Metro North), synthesize positions from
        # TripUpdate data using each trip's current/next stop lat/lon.
        if not vehicles and self._gtfs.is_loaded():
            for entity in feed.entity:
                if entity.HasField("trip_update"):
                    v = self._synthesize_vehicle(entity.trip_update, now_ts)
                    if v and v["vehicle_id"] not in vehicles:
                        vehicles[v["vehicle_id"]] = v

        return stops, list(vehicles.values()), alerts

    def _parse_trip_update(
        self,
        tu: Any,
        stops: dict[str, list],
        now_ts: float,
    ) -> None:
        trip_id = tu.trip.trip_id
        route_id = tu.trip.route_id
        direction = tu.trip.direction_id
        static_stops = self._gtfs.get_trip_stops(trip_id) if self._gtfs.is_loaded() else []
        vehicle_label = tu.vehicle.label if tu.HasField("vehicle") else ""

        for stu in tu.stop_time_update:
            stop_id = stu.stop_id
            if stop_id not in stops:
                continue

            scheduled_ts: int | None = None
            delay_seconds = 0

            if stu.HasField("departure") and stu.departure.time:
                scheduled_ts = stu.departure.time
                delay_seconds = stu.departure.delay or 0
            elif stu.HasField("arrival") and stu.arrival.time:
                scheduled_ts = stu.arrival.time
                delay_seconds = stu.arrival.delay or 0

            if scheduled_ts is None:
                continue

            estimated_ts = scheduled_ts + delay_seconds
            if estimated_ts < now_ts - 60:
                continue

            delay_minutes = round(delay_seconds / 60)

            # Pull track and official MTA status from MTARR extension (field 1005).
            # Fall back to vehicle.label for track when the extension is absent.
            ext_track, mta_status = extract_stop_time_update_ext(stu)
            track = _sanitize(ext_track or vehicle_label, _MAX_TRACK_LEN)
            status = _sanitize(mta_status, _MAX_STATUS_LEN) if mta_status else _train_status(delay_minutes)

            train_number = (
                self._gtfs.get_trip_short_name(trip_id) if self._gtfs.is_loaded() else ""
            ) or trip_id

            stops[stop_id].append(
                {
                    "trip_id": trip_id,
                    "train_number": train_number,
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
                    "status": status,
                    "track": track,
                    "stop_sequence": stu.stop_sequence,
                    "destination": self._resolve_terminus(tu, static_stops, last=True),
                    "origin": self._resolve_terminus(tu, static_stops, last=False),
                    "trip_stops": self._build_upcoming_stops(static_stops, stu.stop_sequence),
                }
            )

    def _parse_vehicle_entity(self, vp: Any, entity_id: str) -> dict | None:
        try:
            lat = vp.position.latitude
            lon = vp.position.longitude
        except Exception:
            return None
        if lat == 0.0 and lon == 0.0:
            return None

        trip_id = vp.trip.trip_id if vp.HasField("trip") else ""
        route_id = vp.trip.route_id if vp.HasField("trip") else ""
        current_stop_id = vp.stop_id or ""
        current_stop_name = (
            self._gtfs.get_stop_name(current_stop_id)
            if self._gtfs.is_loaded()
            else FALLBACK_STATIONS.get(current_stop_id, current_stop_id)
        )
        raw_stops = self._gtfs.get_trip_stops(trip_id) if self._gtfs.is_loaded() else []
        trip_stops = [
            {
                "stop_sequence": s.stop_sequence,
                "stop_name": s.stop_name,
                "arrival": s.arrival_time,
                "departure": s.departure_time,
            }
            for s in raw_stops
        ]
        train_number = (
            self._gtfs.get_trip_short_name(trip_id) if self._gtfs.is_loaded() else ""
        ) or vp.vehicle.label or vp.vehicle.id or entity_id
        return {
            "vehicle_id": vp.vehicle.id or entity_id,
            "label": vp.vehicle.label or vp.vehicle.id or entity_id,
            "train_number": train_number,
            "trip_id": trip_id,
            "route_id": route_id,
            "route_name": self._gtfs.get_route_name(route_id) if self._gtfs.is_loaded() else route_id,
            "headsign": self._get_headsign(trip_id),
            "latitude": lat,
            "longitude": lon,
            "bearing": vp.position.bearing,
            "speed": round(vp.position.speed * 2.237, 1) if vp.position.speed else 0,
            "current_stop_id": current_stop_id,
            "current_stop_name": current_stop_name,
            "current_stop_sequence": vp.current_stop_sequence,
            "timestamp": vp.timestamp,
            "trip_stops": trip_stops,
        }

    def _parse_alert(
        self,
        alert: Any,
        alerts: dict[str, list],
        now_ts: float,
    ) -> None:
        """Parse a GTFS-RT Alert entity and index it by affected stop_id and route_id."""
        # Check active period — skip alerts that are wholly in the past
        active = True
        for period in alert.active_period:
            end = period.end
            if end and end < now_ts - 300:
                active = False
                break
        if not active:
            return

        header = get_translated_text(alert.header_text)
        description = get_translated_text(alert.description_text)

        # CAUSE / EFFECT enums → human-readable strings
        cause = _alert_enum_name(alert.cause, _ALERT_CAUSE)
        effect = _alert_enum_name(alert.effect, _ALERT_EFFECT)

        # Mercury extension
        mercury = extract_mercury_alert(alert)
        alert_type = mercury.get("alert_type", "")
        human_period = mercury.get("human_readable_active_period", "")

        alert_dict = {
            "header": header,
            "description": description,
            "cause": cause,
            "effect": effect,
            "alert_type": alert_type,
            "active_period": human_period,
        }

        # Index by each informed entity's stop_id and/or route_id
        for ie in alert.informed_entity:
            mercury_sel = extract_mercury_entity_selector(ie)
            sort_order = mercury_sel.get("sort_order", "")

            keys: list[str] = []
            if ie.stop_id:
                keys.append(ie.stop_id)
            if ie.route_id:
                keys.append(f"route:{ie.route_id}")

            for key in keys:
                if key not in alerts:
                    alerts[key] = []
                entry = {**alert_dict}
                if sort_order:
                    entry["priority"] = sort_order
                alerts[key].append(entry)

    def _synthesize_vehicle(self, tu: Any, now_ts: float) -> dict | None:
        """Build an approximate vehicle from TripUpdate + static GTFS stop coordinates.

        Used when no real VehiclePosition data is available.  The pin appears at
        the train's current or next scheduled stop.
        """
        if not self._gtfs.is_loaded():
            return None

        trip_id = tu.trip.trip_id
        route_id = tu.trip.route_id

        # Find the first stop that hasn't been departed yet.
        current_stop_id = None
        current_stop_seq = 0
        for stu in tu.stop_time_update:
            dep_ts = 0
            if stu.HasField("departure") and stu.departure.time:
                dep_ts = stu.departure.time + (stu.departure.delay or 0)
            elif stu.HasField("arrival") and stu.arrival.time:
                dep_ts = stu.arrival.time + (stu.arrival.delay or 0)
            if dep_ts and dep_ts >= now_ts - 120:
                current_stop_id = stu.stop_id
                current_stop_seq = stu.stop_sequence
                break

        if not current_stop_id:
            return None

        stop_info = self._gtfs.data.stops.get(current_stop_id)
        if not stop_info or (stop_info.lat == 0.0 and stop_info.lon == 0.0):
            return None

        vehicle_id = (tu.vehicle.id if tu.HasField("vehicle") and tu.vehicle.id else trip_id)
        vehicle_label = (tu.vehicle.label if tu.HasField("vehicle") and tu.vehicle.label else trip_id)

        raw_stops = self._gtfs.get_trip_stops(trip_id)
        trip_stops = [
            {
                "stop_sequence": s.stop_sequence,
                "stop_name": s.stop_name,
                "arrival": s.arrival_time,
                "departure": s.departure_time,
            }
            for s in raw_stops
        ]

        train_number = self._gtfs.get_trip_short_name(trip_id) or vehicle_label
        return {
            "vehicle_id": _sanitize(vehicle_id, 64),
            "label": _sanitize(vehicle_label, _MAX_TRACK_LEN),
            "train_number": _sanitize(train_number, _MAX_TRACK_LEN),
            "trip_id": trip_id,
            "route_id": route_id,
            "route_name": self._gtfs.get_route_name(route_id),
            "headsign": self._get_headsign(trip_id),
            "latitude": stop_info.lat,
            "longitude": stop_info.lon,
            "bearing": 0,
            "speed": 0,
            "current_stop_id": current_stop_id,
            "current_stop_name": stop_info.name,
            "current_stop_sequence": current_stop_seq,
            "timestamp": int(now_ts),
            "trip_stops": trip_stops,
        }

    def _supplement_vehicles(self, vehicles: dict[str, dict]) -> None:
        """Try the dedicated vehicle feed and merge any new positions found."""
        try:
            resp = requests.get(
                GTFS_RT_VEHICLES_URL, headers=self._headers, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(resp.content)
            for entity in feed.entity:
                if entity.HasField("vehicle"):
                    v = self._parse_vehicle_entity(entity.vehicle, entity.id)
                    if v and v["vehicle_id"] not in vehicles:
                        vehicles[v["vehicle_id"]] = v
        except Exception as err:
            _LOGGER.debug("Dedicated vehicle feed unavailable: %s", err)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_headsign(self, trip_id: str) -> str:
        if not self._gtfs.is_loaded():
            return ""
        info = self._gtfs.get_trip_info(trip_id)
        return info.headsign if info else ""

    @staticmethod
    def _build_upcoming_stops(static_stops: list, current_sequence: int) -> list[dict]:
        return [
            {
                "stop_sequence": s.stop_sequence,
                "stop_name": s.stop_name,
                "arrival": s.arrival_time,
                "departure": s.departure_time,
            }
            for s in static_stops
            if s.stop_sequence >= current_sequence
        ]

    def _resolve_terminus(self, trip_update: Any, static_stops: list, last: bool) -> str:
        if static_stops:
            return (static_stops[-1] if last else static_stops[0]).stop_name
        rt = list(trip_update.stop_time_update)
        if not rt:
            return "Unknown"
        stop_id = rt[-1].stop_id if last else rt[0].stop_id
        if self._gtfs.is_loaded():
            return self._gtfs.get_stop_name(stop_id)
        return FALLBACK_STATIONS.get(stop_id, stop_id)
