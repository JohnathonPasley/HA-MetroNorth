"""GTFS static data manager — downloads and caches Metro North schedule data."""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

GTFS_STATIC_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip"
REFRESH_INTERVAL = timedelta(hours=24)
REQUEST_TIMEOUT = 60

# Column names to probe for track/platform info in stop_times.txt / stops.txt
_TRACK_COLS_STOP_TIMES = ("track", "arrival_platform", "departure_platform", "platform")
_TRACK_COLS_STOPS = ("platform_code", "stop_code", "track")


@dataclass(slots=True)
class StopInfo:
    stop_id: str
    name: str
    lat: float
    lon: float
    platform_code: str = ""  # from platform_code / stop_code in stops.txt


@dataclass(slots=True)
class StopTimeInfo:
    stop_sequence: int
    stop_id: str
    stop_name: str
    arrival_time: str
    departure_time: str
    track: str = ""  # from track / platform column in stop_times.txt (non-standard)
    pickup_type: int = 0
    drop_off_type: int = 0


@dataclass(slots=True)
class ServiceCalendar:
    days: frozenset  # weekday ints 0=Mon..6=Sun the service runs
    start_date: str  # YYYYMMDD
    end_date: str    # YYYYMMDD


@dataclass(slots=True)
class TripInfo:
    trip_id: str
    route_id: str
    headsign: str
    direction_id: int
    service_id: str = ""
    short_name: str = ""  # trip_short_name → human-readable train number, e.g. "509"

    @property
    def direction_text(self) -> str:
        # Metro North: direction_id 0 = Inbound (toward Grand Central), 1 = Outbound
        return "Inbound" if self.direction_id == 0 else "Outbound"


class GTFSStaticData:
    def __init__(self) -> None:
        self.stops: dict[str, StopInfo] = {}
        self.trips: dict[str, TripInfo] = {}
        self.stop_times: dict[str, list[StopTimeInfo]] = {}
        self.routes: dict[str, str] = {}
        self.short_name_index: dict[str, str] = {}  # trip_short_name → trip_id
        self.last_updated: datetime | None = None
        self.calendar: dict[str, ServiceCalendar] = {}  # service_id → calendar
        self.calendar_exceptions: dict[str, dict[str, int]] = {}  # service_id → {date: exception_type}


class GTFSStaticManager:
    """Downloads and parses Metro North static GTFS data, refreshed daily."""

    def __init__(self, hass: Any) -> None:
        self._hass = hass
        self.data: GTFSStaticData = GTFSStaticData()

    async def async_ensure_loaded(self) -> None:
        """Load if never loaded or stale (> 24 h)."""
        if (
            self.data.last_updated is None
            or datetime.now(timezone.utc) - self.data.last_updated > REFRESH_INTERVAL
        ):
            await self._hass.async_add_executor_job(self._fetch_and_parse)

    async def async_force_refresh(self) -> None:
        await self._hass.async_add_executor_job(self._fetch_and_parse)

    def _fetch_and_parse(self) -> None:
        _LOGGER.info("Downloading Metro North static GTFS from %s", GTFS_STATIC_URL)
        tmp_path: str | None = None
        try:
            # Stream directly to a temp file so we never hold the full zip in RAM.
            with requests.get(GTFS_STATIC_URL, timeout=REQUEST_TIMEOUT, stream=True) as resp:
                resp.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                    tmp_path = tmp.name
                    for chunk in resp.iter_content(chunk_size=65536):
                        tmp.write(chunk)
        except requests.RequestException as err:
            _LOGGER.error("Failed to download static GTFS: %s", err)
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return

        data = GTFSStaticData()

        try:
            with zipfile.ZipFile(tmp_path) as zf:
                names = zf.namelist()

                if "stops.txt" in names:
                    with zf.open("stops.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        fieldnames = reader.fieldnames or []
                        plat_col = next((c for c in _TRACK_COLS_STOPS if c in fieldnames), None)
                        for row in reader:
                            try:
                                data.stops[row["stop_id"]] = StopInfo(
                                    stop_id=row["stop_id"],
                                    name=row["stop_name"].strip(),
                                    lat=float(row.get("stop_lat") or 0),
                                    lon=float(row.get("stop_lon") or 0),
                                    platform_code=row.get(plat_col, "").strip() if plat_col else "",
                                )
                            except (KeyError, ValueError):
                                pass

                if "routes.txt" in names:
                    with zf.open("routes.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            name = (
                                row.get("route_long_name")
                                or row.get("route_short_name", "")
                            ).strip()
                            data.routes[row["route_id"]] = name

                if "trips.txt" in names:
                    with zf.open("trips.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            try:
                                data.trips[row["trip_id"]] = TripInfo(
                                    trip_id=row["trip_id"],
                                    route_id=row["route_id"],
                                    headsign=row.get("trip_headsign", "").strip(),
                                    direction_id=int(row.get("direction_id") or 0),
                                    service_id=row.get("service_id", "").strip(),
                                    short_name=row.get("trip_short_name", "").strip(),
                                )
                            except (KeyError, ValueError):
                                pass

                # Build short_name index for cross-referencing RT trip_ids to static trips.
                # Per MTA: RT trip.trip_id matches static trip_short_name (different systems).
                data.short_name_index = {
                    info.short_name: tid
                    for tid, info in data.trips.items()
                    if info.short_name
                }

                active_stop_ids: set[str] = set()
                if "stop_times.txt" in names:
                    with zf.open("stop_times.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        fieldnames = reader.fieldnames or []
                        track_col = next((c for c in _TRACK_COLS_STOP_TIMES if c in fieldnames), None)
                        for row in reader:
                            trip_id = row.get("trip_id")
                            if not trip_id:
                                continue
                            stop_id = row.get("stop_id", "")
                            active_stop_ids.add(stop_id)
                            stop_info = data.stops.get(stop_id)
                            stop_name = stop_info.name if stop_info else stop_id
                            entry = StopTimeInfo(
                                stop_sequence=int(row.get("stop_sequence") or 0),
                                stop_id=stop_id,
                                stop_name=stop_name,
                                arrival_time=row.get("arrival_time", ""),
                                departure_time=row.get("departure_time", ""),
                                track=row.get(track_col, "").strip() if track_col else "",
                                pickup_type=int(row.get("pickup_type") or 0),
                                drop_off_type=int(row.get("drop_off_type") or 0),
                            )
                            if trip_id not in data.stop_times:
                                data.stop_times[trip_id] = []
                            data.stop_times[trip_id].append(entry)

                    for tid in data.stop_times:
                        data.stop_times[tid].sort(key=lambda x: x.stop_sequence)

                if "calendar.txt" in names:
                    _DAY_COLS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
                    with zf.open("calendar.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            days = frozenset(i for i, col in enumerate(_DAY_COLS) if row.get(col) == "1")
                            data.calendar[row["service_id"]] = ServiceCalendar(
                                days=days,
                                start_date=row.get("start_date", ""),
                                end_date=row.get("end_date", ""),
                            )

                if "calendar_dates.txt" in names:
                    with zf.open("calendar_dates.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            sid = row["service_id"]
                            if sid not in data.calendar_exceptions:
                                data.calendar_exceptions[sid] = {}
                            data.calendar_exceptions[sid][row["date"]] = int(row.get("exception_type") or 0)

                # Only expose stops that have scheduled service in this feed
                if active_stop_ids:
                    data.stops = {k: v for k, v in data.stops.items() if k in active_stop_ids}

        except Exception as err:
            _LOGGER.error("Error parsing static GTFS ZIP: %s", err)
            return
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        data.last_updated = datetime.now(timezone.utc)
        self.data = data
        _LOGGER.info(
            "Loaded Metro North GTFS: %d stops, %d trips, %d trip-stop entries",
            len(data.stops),
            len(data.trips),
            sum(len(v) for v in data.stop_times.values()),
        )

    # ── Convenience accessors ──────────────────────────────────────────────

    def is_loaded(self) -> bool:
        return self.data.last_updated is not None

    def last_updated_iso(self) -> str:
        return self.data.last_updated.isoformat() if self.data.last_updated else ""

    def get_stop_name(self, stop_id: str) -> str:
        s = self.data.stops.get(stop_id)
        return s.name if s else stop_id

    def get_all_stops(self) -> dict[str, StopInfo]:
        return self.data.stops

    def resolve_trip_id(self, rt_trip_id: str) -> str:
        """Resolve an RT trip_id to the static GTFS trip_id.

        MTA uses different ID systems for the RT feed and static GTFS.
        RT trip.trip_id should match static trip_short_name; try that
        cross-reference when a direct trip_id lookup fails.
        """
        if rt_trip_id in self.data.trips:
            return rt_trip_id
        resolved = self.data.short_name_index.get(rt_trip_id)
        if resolved:
            return resolved
        return rt_trip_id

    def get_trip_stops(self, trip_id: str) -> list[StopTimeInfo]:
        return self.data.stop_times.get(trip_id, [])

    def get_trip_info(self, trip_id: str) -> TripInfo | None:
        return self.data.trips.get(trip_id)

    def get_raw_trip_short_name(self, trip_id: str) -> str:
        """Return trip_short_name exactly as it appears in trips.txt."""
        info = self.data.trips.get(trip_id)
        return info.short_name if info else ""

    def get_trip_short_name(self, trip_id: str) -> str:
        """Return the human-readable train number for display (e.g. '509').

        Uses trip_short_name when it looks like a real train number (2–5 digits).
        Otherwise falls back to extracting a 3–4 digit number from trip_id,
        which is where Metro North embeds the schedule number.
        """
        info = self.data.trips.get(trip_id)
        short = info.short_name if info else ""
        if short and re.fullmatch(r"\d{2,5}", short):
            return short
        m = re.search(r"(?<!\d)(\d{3,4})(?!\d)", trip_id)
        if m:
            return m.group(1)
        return short

    def get_route_name(self, route_id: str) -> str:
        return self.data.routes.get(route_id, route_id)

    def sorted_stop_names(self) -> list[str]:
        """All stop names sorted alphabetically."""
        return sorted({s.name for s in self.data.stops.values()})

    def stop_id_for_name(self, name: str) -> str | None:
        for s in self.data.stops.values():
            if s.name == name:
                return s.stop_id
        return None

    def trip_runs_on_date(self, trip_id: str, check_date: "datetime") -> bool:
        """Return True if the trip is scheduled to run on check_date."""
        trip = self.data.trips.get(trip_id)
        if not trip or not trip.service_id:
            return True
        sid = trip.service_id
        date_str = check_date.strftime("%Y%m%d")
        weekday = check_date.weekday()
        exc = self.data.calendar_exceptions.get(sid, {}).get(date_str)
        if exc == 1:
            return True
        if exc == 2:
            return False
        cal = self.data.calendar.get(sid)
        if not cal:
            return True
        if date_str < cal.start_date or date_str > cal.end_date:
            return False
        return weekday in cal.days
