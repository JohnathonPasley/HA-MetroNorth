"""Data update coordinator for MTA Metro North."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any

import requests
from google.transit import gtfs_realtime_pb2

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    FALLBACK_STATIONS,
    GTFS_RT_ALERTS_URL,
    GTFS_RT_URL,
)
from .gtfs_static import GTFSStaticManager
from .mta_extensions import extract_stop_time_update_ext, extract_stop_time_update_ext_debug
from .mta_mercury import extract_mercury_alert, extract_mercury_entity_selector, get_translated_text

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
_MAX_TRACK_LEN = 16
_MAX_STATUS_LEN = 64
_MAX_TRIP_STOPS = 50  # cap trip_stops list stored per train to bound coordinator RAM

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


# Service type classification: stop names that indicate local service
_LOCAL_INDICATOR_STOPS = frozenset({"Woodlawn", "Bronxville", "Tuckahoe"})
# If a train doesn't stop here it's Super Express (Harlem Line specific)
_SUPER_EXPRESS_STOP = "harlem-125"  # matches "Harlem-125 St" from actual GTFS stops.txt


def _alert_enum_name(val: int, mapping: dict) -> str:
    return mapping.get(val, "")


def _sanitize(s: str, max_len: int) -> str:
    """Strip non-printable characters and truncate to max_len."""
    return "".join(c for c in s if c.isprintable())[:max_len]


class PeakWindow:
    def __init__(self, start: str, end: str, interval: int, days: set[int] | None = None) -> None:
        self.start = _parse_time(start)
        self.end = _parse_time(end)
        self.interval = interval
        self.days: set[int] = days if days is not None else set()  # empty = every day

    def is_active(self, now: time) -> bool:
        if self.days and datetime.now().weekday() not in self.days:
            return False
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


# Trains terminating at any of these stations are heading toward Grand Central = Inbound.
# Yankees-E 153 St is a Hudson Line stub terminal south of other Hudson stops.
_INBOUND_TERMINALS = ("grand central", "yankees", "harlem-125 st")


def _infer_direction(destination: str) -> int:
    """Infer Metro North direction from destination name.

    direction_id in the RT feed defaults to 0 for all trains and is unreliable.
    Grand Central Terminal is the terminal for all inbound Metro North trains.
    """
    return 0 if any(p in destination.lower() for p in _INBOUND_TERMINALS) else 1


class MetroNorthCoordinator(DataUpdateCoordinator):
    """Coordinator — fetches GTFS-RT on a self-managed schedule.

    When peak windows are configured, polls only during those windows and sleeps
    between them (zero API calls off-hours). Falls back to continuous polling at
    default_interval when no peak windows are defined.
    """

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
        self._unsub_poll: Any = None

        # update_interval=None: HA's built-in scheduler is disabled.
        # We manage our own schedule via async_call_later so we can sleep
        # between peak windows without any timer overhead.
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )

    # ── Polling lifecycle ──────────────────────────────────────────────────

    def start_polling(self) -> None:
        """Start the self-scheduling poll loop. Call once after first refresh."""
        self._schedule_next_poll()

    def stop_polling(self) -> None:
        """Cancel any pending scheduled poll."""
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None

    def _get_current_interval(self) -> int | None:
        """Return poll interval in seconds for right now, or None to sleep.

        None means we are between peak windows; _schedule_next_poll will
        compute the exact wake-up time so polling resumes automatically.
        """
        now = datetime.now().time()
        for window in self._peak_windows:
            if window.is_active(now):
                return window.interval
        # Outside all peak windows: sleep if any windows are defined,
        # otherwise fall back to continuous polling at the default rate.
        if self._peak_windows:
            return None
        return self._default_interval

    def _seconds_until_next_window(self) -> int:
        """Seconds until the earliest upcoming peak window starts."""
        now_dt = datetime.now()
        for days_ahead in range(8):
            check_dt = now_dt + timedelta(days=days_ahead)
            check_day = check_dt.weekday()
            best_today: datetime | None = None
            for window in self._peak_windows:
                if window.days and check_day not in window.days:
                    continue
                candidate = check_dt.replace(
                    hour=window.start.hour,
                    minute=window.start.minute,
                    second=0,
                    microsecond=0,
                )
                if candidate <= now_dt:
                    continue
                if best_today is None or candidate < best_today:
                    best_today = candidate
            if best_today is not None:
                return max(60, int((best_today - now_dt).total_seconds()))
        return 3600  # no window found in next 8 days — check again in an hour

    def _schedule_next_poll(self) -> None:
        """Schedule the next poll or window-wake using async_call_later."""
        self.stop_polling()
        interval = self._get_current_interval()
        if interval is not None:
            delay = interval
            _LOGGER.debug("Metro North: next poll in %ds (active window)", delay)
        else:
            delay = self._seconds_until_next_window()
            _LOGGER.debug("Metro North: sleeping %ds until next active window", delay)

        async def _cb(_now: Any) -> None:
            self._unsub_poll = None
            await self.async_refresh()
            self._schedule_next_poll()

        self._unsub_poll = async_call_later(self.hass, delay, _cb)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._gtfs.async_ensure_loaded()
        except Exception as err:
            _LOGGER.warning("Static GTFS refresh failed (non-fatal): %s", err)

        try:
            trip_data, alert_data = await self.hass.async_add_executor_job(
                self._fetch_all_rt
            )
        except requests.RequestException as err:
            raise UpdateFailed(f"Error fetching Metro North data: {err}") from err

        return {
            "trip_updates": trip_data,
            "service_alerts": alert_data,
            "last_updated": datetime.now(timezone.utc),
        }

    # ── Combined GTFS-RT fetch ─────────────────────────────────────────────

    def _fetch_all_rt(self) -> tuple[dict[str, list], dict[str, list]]:
        """Fetch main feed; parse trip updates and service alerts."""
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

        now_ts = datetime.now(timezone.utc).timestamp()

        for entity in feed.entity:
            if entity.HasField("trip_update"):
                self._parse_trip_update(entity.trip_update, stops, now_ts, entity.id)

        # Fetch alerts from the dedicated Metro North alerts endpoint
        alerts = self._fetch_alerts_rt(now_ts)

        # Sort each stop by estimated departure
        for sid in stops:
            stops[sid].sort(key=lambda x: x["estimated_time"])

        return stops, alerts

    def _parse_trip_update(
        self,
        tu: Any,
        stops: dict[str, list],
        now_ts: float,
        entity_id: str = "",
    ) -> None:
        rt_trip_id = tu.trip.trip_id
        route_id = tu.trip.route_id
        vehicle_label = tu.vehicle.label if tu.HasField("vehicle") else ""

        static_trip_id = self._gtfs.resolve_trip_id(rt_trip_id) if self._gtfs.is_loaded() else rt_trip_id
        static_stops = self._gtfs.get_trip_stops(static_trip_id) if self._gtfs.is_loaded() else []

        destination = self._resolve_terminus(tu, static_stops, last=True)
        origin = self._resolve_terminus(tu, static_stops, last=False)
        direction = _infer_direction(destination)
        train_number = entity_id or vehicle_label or rt_trip_id

        # Compute position once per trip — same for all stops
        position = self._estimate_current_stop(tu.stop_time_update, now_ts)
        service_type = self._classify_service_type(static_stops)
        if not service_type and self._gtfs.is_loaded():
            service_type = self._classify_service_type_rt(tu.stop_time_update)

        # Pre-scan all stop_time_updates for MTARR track extension.
        # Track is typically only present at departure stops (Grand Central),
        # so cache it and reuse for every stop of the same trip.
        trip_track = ""
        trip_ext_raw = ""
        for _stu in tu.stop_time_update:
            t, _, raw = extract_stop_time_update_ext_debug(_stu)
            if t:
                trip_track = t
                trip_ext_raw = raw
                break
            elif raw and not trip_ext_raw:
                trip_ext_raw = raw  # capture raw bytes even when track isn't set yet

        for stu in tu.stop_time_update:
            stop_id = stu.stop_id
            if stop_id not in stops:
                continue

            estimated_ts: int | None = None
            delay_seconds = 0

            if stu.HasField("departure") and stu.departure.time:
                # departure.time IS the estimated time in the MTA feed (delay already applied).
                # departure.delay is the offset from schedule used to derive scheduled_ts.
                estimated_ts = stu.departure.time
                delay_seconds = stu.departure.delay or 0
            elif stu.HasField("arrival") and stu.arrival.time:
                estimated_ts = stu.arrival.time
                delay_seconds = stu.arrival.delay or 0

            if estimated_ts is None:
                continue

            scheduled_ts = estimated_ts - delay_seconds
            if estimated_ts < now_ts - 60:
                continue

            delay_minutes = round(delay_seconds / 60)

            ext_track, mta_status = extract_stop_time_update_ext(stu)
            static_track = ""
            if not ext_track and not trip_track and static_stops:
                static_track = next(
                    (s.track for s in static_stops if s.stop_id == stop_id and s.track),
                    "",
                )
            track = _sanitize(ext_track or trip_track or static_track, _MAX_TRACK_LEN)
            status = _sanitize(mta_status, _MAX_STATUS_LEN) if mta_status else _train_status(delay_minutes)

            minutes_until = (estimated_ts - now_ts) / 60
            if delay_minutes < -1:
                departure_status = f"Running {abs(delay_minutes)} min Early"
            elif minutes_until <= 1:
                departure_status = "Stand Clear of the Closing Doors Please, Departing"
            elif minutes_until <= 11:
                departure_status = "Scheduled to Depart Soon"
            else:
                departure_status = "Scheduled Departure"

            stops_to_station = self._calc_stops_to_station(static_stops, position, stop_id)

            stops[stop_id].append(
                {
                    "trip_id": rt_trip_id,
                    "train_number": train_number,
                    "route_id": route_id,
                    "route_name": self._gtfs.get_route_name(route_id)
                    if self._gtfs.is_loaded()
                    else route_id,
                    "direction": direction,
                    "headsign": self._get_headsign(static_trip_id),
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
                    "destination": destination,
                    "origin": origin,
                    "current_stop": position["stop_name"],
                    "current_stop_id": position["stop_id"],
                    "next_stop": position["next_stop_name"],
                    "is_en_route": position["is_en_route"],
                    "stops_remaining": position["stops_remaining"],
                    "stops_to_station": stops_to_station,
                    "service_type": service_type,
                    "departure_status": departure_status,
                    "latitude": position["lat"],
                    "longitude": position["lon"],
                    "mtarr_raw": trip_ext_raw,
                    "trip_stops": (
                        self._build_upcoming_stops(static_stops, stu.stop_sequence)
                        if static_stops
                        else self._build_rt_stops(tu.stop_time_update, stu.stop_sequence)
                    )[:_MAX_TRIP_STOPS],
                }
            )

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

        # Index by each informed entity's stop_id and/or route_id.
        # Agency-only entities (no stop_id, no route_id) go to "_agency".
        indexed_keys: set[str] = set()
        for ie in alert.informed_entity:
            mercury_sel = extract_mercury_entity_selector(ie)
            sort_order = mercury_sel.get("sort_order", "")

            keys: list[str] = []
            if ie.stop_id:
                keys.append(ie.stop_id)
            if ie.route_id:
                keys.append(f"route:{ie.route_id}")
            if not keys:
                keys.append("_agency")

            for key in keys:
                indexed_keys.add(key)
                if key not in alerts:
                    alerts[key] = []
                entry = {**alert_dict}
                if sort_order:
                    entry["priority"] = sort_order
                alerts[key].append(entry)

        # Always add every alert to "_all" for sensors that want the full list
        if "_all" not in alerts:
            alerts["_all"] = []
        alerts["_all"].append(alert_dict)

    def _fetch_alerts_rt(self, now_ts: float) -> dict[str, list]:
        """Fetch service alerts from the dedicated Metro North alerts endpoint."""
        alerts: dict[str, list] = {}
        try:
            resp = requests.get(
                GTFS_RT_ALERTS_URL, headers=self._headers, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(resp.content)
            for entity in feed.entity:
                if entity.HasField("alert"):
                    self._parse_alert(entity.alert, alerts, now_ts)
        except Exception as err:
            _LOGGER.debug("Metro North alerts feed unavailable: %s", err)
        return alerts

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

    def _build_rt_stops(self, stop_time_updates: Any, from_sequence: int) -> list[dict]:
        """Build trip stops from RT stop_time_update when static GTFS has no match."""
        result = []
        for stu in stop_time_updates:
            if stu.stop_sequence < from_sequence:
                continue
            stop_name = (
                self._gtfs.get_stop_name(stu.stop_id)
                if self._gtfs.is_loaded()
                else FALLBACK_STATIONS.get(stu.stop_id, stu.stop_id)
            )
            arr_ts = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else None
            dep_ts = stu.departure.time if stu.HasField("departure") and stu.departure.time else None
            result.append({
                "stop_sequence": stu.stop_sequence,
                "stop_name": stop_name,
                "arrival": datetime.fromtimestamp(arr_ts, tz=timezone.utc).isoformat() if arr_ts else "",
                "departure": datetime.fromtimestamp(dep_ts, tz=timezone.utc).isoformat() if dep_ts else "",
            })
        return result

    def _estimate_current_stop(
        self, stop_time_updates: Any, now_ts: float
    ) -> dict[str, Any]:
        """Return positioning dict for a train based on its RT stop_time_updates.

        Interpolates location between last-departed and next upcoming stop when
        the train has been moving for more than 90 seconds.
        """
        sorted_stus = sorted(stop_time_updates, key=lambda s: s.stop_sequence)

        events: list[tuple[float, str, int]] = []
        for stu in sorted_stus:
            dep_ts: float = 0
            if stu.HasField("departure") and stu.departure.time:
                dep_ts = float(stu.departure.time)  # departure.time IS estimated; don't add delay
            elif stu.HasField("arrival") and stu.arrival.time:
                dep_ts = float(stu.arrival.time)
            if dep_ts:
                events.append((dep_ts, stu.stop_id, stu.stop_sequence))

        _empty: dict[str, Any] = {
            "stop_name": "", "stop_id": "", "stop_sequence": 0,
            "stops_remaining": 0, "lat": None, "lon": None,
            "is_en_route": False, "next_stop_name": "", "next_stop_id": "",
        }
        if not events:
            return _empty

        stops_remaining = sum(1 for t, _, _ in events if t > now_ts)

        # Find the last stop the train has departed
        last_idx = -1
        for i, (dep_ts, _, _) in enumerate(events):
            if dep_ts <= now_ts:
                last_idx = i

        if last_idx == -1:
            # Train has not yet departed its first stop
            _, stop_id, stop_seq = events[0]
            lat, lon = self._coords(stop_id)
            return {
                **_empty,
                "stop_name": self._stop_name(stop_id),
                "stop_id": stop_id,
                "stop_sequence": stop_seq,
                "stops_remaining": stops_remaining,
                "lat": lat, "lon": lon,
            }

        dep_ts, stop_id, stop_seq = events[last_idx]
        next_stop_id = ""
        next_stop_name = ""
        is_en_route = False

        next_idx = last_idx + 1
        if next_idx < len(events):
            next_dep_ts, next_stop_id, _ = events[next_idx]
            next_stop_name = self._stop_name(next_stop_id)
            # Mark as en route if > 90 s since departing last stop
            if now_ts - dep_ts > 90:
                is_en_route = True

        if is_en_route:
            display_name = f"En Route to {next_stop_name}"
            next_dep_ts = events[next_idx][0]
            frac = min(1.0, max(0.0, (now_ts - dep_ts) / max(1.0, next_dep_ts - dep_ts)))
            lat, lon = self._interp_coords(stop_id, next_stop_id, frac)
        else:
            display_name = self._stop_name(stop_id)
            lat, lon = self._coords(stop_id)

        return {
            "stop_name": display_name,
            "stop_id": stop_id,
            "stop_sequence": stop_seq,
            "stops_remaining": stops_remaining,
            "lat": lat,
            "lon": lon,
            "is_en_route": is_en_route,
            "next_stop_name": next_stop_name,
            "next_stop_id": next_stop_id,
        }

    def _stop_name(self, stop_id: str) -> str:
        if self._gtfs.is_loaded():
            return self._gtfs.get_stop_name(stop_id)
        return FALLBACK_STATIONS.get(stop_id, stop_id)

    def _coords(self, stop_id: str) -> tuple[float | None, float | None]:
        if self._gtfs.is_loaded():
            si = self._gtfs.data.stops.get(stop_id)
            if si and not (si.lat == 0.0 and si.lon == 0.0):
                return si.lat, si.lon
        return None, None

    def _interp_coords(
        self, from_id: str, to_id: str, frac: float
    ) -> tuple[float | None, float | None]:
        from_lat, from_lon = self._coords(from_id)
        to_lat, to_lon = self._coords(to_id)
        if None in (from_lat, from_lon, to_lat, to_lon):
            return from_lat, from_lon
        return (
            from_lat + (to_lat - from_lat) * frac,  # type: ignore[operator]
            from_lon + (to_lon - from_lon) * frac,  # type: ignore[operator]
        )

    def _calc_stops_to_station(
        self, static_stops: list, position: dict, target_stop_id: str
    ) -> int:
        """Count stops between current position and target station."""
        if not static_stops:
            return 0
        ids = [s.stop_id for s in static_stops]
        try:
            target_idx = ids.index(target_stop_id)
        except ValueError:
            return 0
        if position.get("is_en_route") and position.get("next_stop_id"):
            try:
                start_idx = ids.index(position["next_stop_id"])
                # En route: count from next_stop inclusive to target inclusive
                return max(0, target_idx - start_idx + 1)
            except ValueError:
                pass
        try:
            current_idx = ids.index(position.get("stop_id", ""))
            return max(0, target_idx - current_idx)
        except ValueError:
            return 0

    @staticmethod
    def _classify_service_type(static_stops: list) -> str:
        """Classify service type from stop pattern.

        Local  — stops at Woodlawn, Bronxville, or Tuckahoe (inner Harlem Line suburbs)
        Super Express — skips Harlem-125th Street entirely
        Express — everything else
        """
        if not static_stops:
            return ""
        stop_names = {s.stop_name for s in static_stops}
        if stop_names & _LOCAL_INDICATOR_STOPS:
            return "Local"
        if not any(_SUPER_EXPRESS_STOP in name.lower() for name in stop_names):
            return "Super Express"
        return "Express"

    def _classify_service_type_rt(self, stop_time_updates: Any) -> str:
        """Classify service type from RT stop_time_updates when static GTFS trip isn't found.

        Uses the same rules as _classify_service_type but resolves names via stop_id lookup.
        """
        stop_names: set[str] = set()
        for stu in stop_time_updates:
            if stu.stop_id:
                stop_names.add(self._stop_name(stu.stop_id).lower())
        if not stop_names:
            return ""
        if stop_names & {"woodlawn", "bronxville", "tuckahoe"}:
            return "Local"
        if not any(_SUPER_EXPRESS_STOP in n for n in stop_names):
            return "Super Express"
        return "Express"

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
