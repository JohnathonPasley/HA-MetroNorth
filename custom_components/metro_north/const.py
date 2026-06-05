"""Constants for MTA Metro North integration."""

DOMAIN = "metro_north"

# Config entry keys
CONF_STATIONS = "stations"
CONF_DIRECTION = "direction"
CONF_NUM_TRAINS = "num_trains"

# Direction options
DIRECTION_BOTH = "both"
DIRECTION_INBOUND = "inbound"    # direction_id == 0, toward Grand Central
DIRECTION_OUTBOUND = "outbound"  # direction_id == 1, away from Grand Central

DEFAULT_NUM_TRAINS = 5
CONF_DEFAULT_INTERVAL = "default_interval"
CONF_PEAK_1_START = "peak_1_start"
CONF_PEAK_1_END = "peak_1_end"
CONF_PEAK_1_INTERVAL = "peak_1_interval"
CONF_PEAK_2_START = "peak_2_start"
CONF_PEAK_2_END = "peak_2_end"
CONF_PEAK_2_INTERVAL = "peak_2_interval"

# Interval bounds (seconds)
MIN_INTERVAL = 15
MAX_INTERVAL = 600
DEFAULT_PEAK_INTERVAL = 30
DEFAULT_OFF_PEAK_INTERVAL = 120

# Peak window defaults
DEFAULT_PEAK_1_START = "07:00"
DEFAULT_PEAK_1_END = "09:00"
DEFAULT_PEAK_2_START = "17:00"
DEFAULT_PEAK_2_END = "20:00"

# Feed URLs
GTFS_RT_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr"
GTFS_RT_VEHICLES_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr-vehicles"
GTFS_STATIC_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip"

# Sensor / tracker attribute names
ATTR_TRAIN_NUMBER = "train_number"
ATTR_TRACK = "track"
ATTR_SCHEDULED_TIME = "scheduled_time"
ATTR_ESTIMATED_TIME = "estimated_time"
ATTR_STATUS = "status"
ATTR_DESTINATION = "destination"
ATTR_ORIGIN = "origin"
ATTR_DELAY_MINUTES = "delay_minutes"
ATTR_UPCOMING_TRAINS = "upcoming_trains"
ATTR_LINE = "line"
ATTR_DIRECTION = "direction"
ATTR_STOP_SEQUENCE = "stop_sequence"
ATTR_VEHICLE_ID = "vehicle_id"
ATTR_SPEED = "speed"
ATTR_BEARING = "bearing"
ATTR_OCCUPANCY = "occupancy"
ATTR_TRIP_STOPS = "trip_stops"
ATTR_HEADSIGN = "headsign"

# Train status labels
TRAIN_STATUS_ON_TIME = "On Time"
TRAIN_STATUS_DELAYED = "Delayed"
TRAIN_STATUS_CANCELLED = "Cancelled"
TRAIN_STATUS_BOARDING = "Boarding"
TRAIN_STATUS_DEPARTED = "Departed"
TRAIN_STATUS_SCHEDULED = "Scheduled"

# ---------------------------------------------------------------------------
# Fallback stop list used only before the static GTFS ZIP has been downloaded.
# Covers Harlem, Hudson, and New Haven lines.
# IDs are approximate — the coordinator replaces them with live GTFS IDs on
# first successful download.
# ---------------------------------------------------------------------------
FALLBACK_STATIONS: dict[str, str] = {
    # ── Shared terminal ──────────────────────────────────────────────────
    "1": "Grand Central Terminal",
    "2": "Harlem-125th Street",
    # ── Harlem Line (GCT → Wassaic) ──────────────────────────────────────
    "3": "Melrose",
    "4": "Tremont",
    "5": "Fordham",
    "6": "Williams Bridge",
    "7": "Woodlawn",
    "8": "Wakefield-241 St",
    "9": "Mount Vernon East",
    "10": "Bronxville",
    "11": "Tuckahoe",
    "12": "Crestwood",
    "13": "Scarsdale",
    "14": "Hartsdale",
    "15": "White Plains",
    "16": "North White Plains",
    "17": "Pleasantville",
    "18": "Hawthorne",
    "19": "Valhalla",
    "20": "Kensico-Wampus",
    "21": "Mount Kisco",
    "22": "Bedford Hills",
    "23": "Katonah",
    "24": "Goldens Bridge",
    "25": "Purdys",
    "26": "Croton Falls",
    "27": "Brewster",
    "28": "Brewster North",
    "29": "Patterson",
    "30": "Pawling",
    "31": "Dover Plains",
    "32": "Harlem Valley-Wingdale",
    "33": "Tenmile River",
    "34": "Wassaic",
    # ── Hudson Line (GCT → Poughkeepsie) ────────────────────────────────
    "35": "Yonkers",
    "36": "Greystone",
    "37": "Hastings-on-Hudson",
    "38": "Dobbs Ferry",
    "39": "Ardsley-on-Hudson",
    "40": "Irvington",
    "41": "Tarrytown",
    "42": "Philipse Manor",
    "43": "Scarborough",
    "44": "Ossining",
    "45": "Croton-Harmon",
    "46": "Cortlandt",
    "47": "Peekskill",
    "48": "Manitou",
    "49": "Cold Spring",
    "50": "Garrison",
    "51": "Breakneck Ridge",
    "52": "Beacon",
    "53": "New Hamburg",
    "54": "Poughkeepsie",
    # ── New Haven Line (GCT → New Haven) ────────────────────────────────
    "55": "Pelham",
    "56": "Mount Vernon West",
    "57": "Fleetwood",
    "58": "Tuckahoe",          # shared with Harlem in practice
    "59": "Larchmont",
    "60": "Mamaroneck",
    "61": "Harrison",
    "62": "Rye",
    "63": "Port Chester",
    "64": "Greenwich",
    "65": "Cos Cob",
    "66": "Riverside",
    "67": "Old Greenwich",
    "68": "Stamford",
    "69": "Noroton Heights",
    "70": "Darien",
    "71": "Rowayton",
    "72": "South Norwalk",
    "73": "East Norwalk",
    "74": "Westport",
    "75": "Green's Farms",
    "76": "Southport",
    "77": "Fairfield",
    "78": "Fairfield Metro",
    "79": "Bridgeport",
    "80": "Stratford",
    "81": "Milford",
    "82": "West Haven",
    "83": "New Haven State Street",
    "84": "New Haven",
    # ── New Haven Line branches ──────────────────────────────────────────
    "85": "New Rochelle",
    "86": "Crestwood",         # shared Harlem/New Haven
    "87": "Tuckahoe",
    "88": "Bronxville",
    "89": "Mount Vernon East",
}

# Reverse lookup used as fallback when GTFS isn't yet loaded
STATION_NAME_TO_ID: dict[str, str] = {v: k for k, v in FALLBACK_STATIONS.items()}

# Keep old name as alias so any third-party code referencing it doesn't break
HARLEM_LINE_STATIONS = FALLBACK_STATIONS
