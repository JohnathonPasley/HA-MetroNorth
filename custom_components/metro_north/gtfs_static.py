"""GTFS static data manager — downloads and caches Metro North schedule data."""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

GTFS_STATIC_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip"
REFRESH_INTERVAL = timedelta(hours=24)
REQUEST_TIMEOUT = 60


@dataclass
class StopInfo:
    stop_id: str
    name: str
    lat: float
    lon: float


@dataclass
class StopTimeInfo:
    stop_sequence: int
    stop_id: str
    stop_name: str
    arrival_time: str
    departure_time: str


@dataclass
class TripInfo:
    trip_id: str
    route_id: str
    headsign: str
    direction_id: int
    short_name: str = ""  # trip_short_name → human-readable train number, e.g. "509"


class GTFSStaticData:
    def __init__(self) -> None:
        self.stops: dict[str, StopInfo] = {}
        self.trips: dict[str, TripInfo] = {}
        self.stop_times: dict[str, list[StopTimeInfo]] = {}
        self.routes: dict[str, str] = {}
        self.last_updated: datetime | None = None


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
        try:
            resp = requests.get(GTFS_STATIC_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as err:
            _LOGGER.error("Failed to download static GTFS: %s", err)
            return

        data = GTFSStaticData()

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                names = zf.namelist()

                if "stops.txt" in names:
                    with zf.open("stops.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                        for row in reader:
                            try:
                                data.stops[row["stop_id"]] = StopInfo(
                                    stop_id=row["stop_id"],
                                    name=row["stop_name"].strip(),
                                    lat=float(row.get("stop_lat") or 0),
                                    lon=float(row.get("stop_lon") or 0),
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
                                    short_name=row.get("trip_short_name", "").strip(),
                                )
                            except (KeyError, ValueError):
                                pass

                active_stop_ids: set[str] = set()
                if "stop_times.txt" in names:
                    with zf.open("stop_times.txt") as f:
                        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
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
                            )
                            if trip_id not in data.stop_times:
                                data.stop_times[trip_id] = []
                            data.stop_times[trip_id].append(entry)

                    for tid in data.stop_times:
                        data.stop_times[tid].sort(key=lambda x: x.stop_sequence)

                # Only expose stops that have scheduled service in this feed
                if active_stop_ids:
                    data.stops = {k: v for k, v in data.stops.items() if k in active_stop_ids}

        except Exception as err:
            _LOGGER.error("Error parsing static GTFS ZIP: %s", err)
            return

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

    def get_stop_name(self, stop_id: str) -> str:
        s = self.data.stops.get(stop_id)
        return s.name if s else stop_id

    def get_all_stops(self) -> dict[str, StopInfo]:
        return self.data.stops

    def get_trip_stops(self, trip_id: str) -> list[StopTimeInfo]:
        return self.data.stop_times.get(trip_id, [])

    def get_trip_info(self, trip_id: str) -> TripInfo | None:
        return self.data.trips.get(trip_id)

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
        # Try to pull a 3–4 digit standalone number from the trip_id
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
