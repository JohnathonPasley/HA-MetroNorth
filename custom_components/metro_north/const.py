"""Constants for MTA Metro North integration."""

DOMAIN = "metro_north"

# Config entry keys
CONF_STATIONS = "stations"
CONF_DIRECTION = "direction"
CONF_NUM_TRAINS = "num_trains"
CONF_DEFAULT_INTERVAL = "default_interval"

# Direction options (stored in config entry)
DIRECTION_BOTH = "both"
DIRECTION_INBOUND = "inbound"    # direction_id = 1 in GTFS (toward Grand Central)
DIRECTION_OUTBOUND = "outbound"  # direction_id = 0 in GTFS (away from Grand Central)

# Defaults
DEFAULT_NUM_TRAINS = 3
CONF_PEAK_1_START = "peak_1_start"
CONF_PEAK_1_END = "peak_1_end"
CONF_PEAK_1_INTERVAL = "peak_1_interval"
CONF_PEAK_2_START = "peak_2_start"
CONF_PEAK_2_END = "peak_2_end"
CONF_PEAK_2_INTERVAL = "peak_2_interval"
CONF_PEAK_1_DAYS = "peak_1_days"
CONF_PEAK_2_DAYS = "peak_2_days"

DEFAULT_PEAK_DAYS = ["0", "1", "2", "3", "4"]  # Mon–Fri (stored as strings for SelectSelector)

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
ATTR_SERVICE_ALERTS = "service_alerts"

# Train status labels
TRAIN_STATUS_ON_TIME = "On Time"
TRAIN_STATUS_DELAYED = "Delayed"
TRAIN_STATUS_CANCELLED = "Cancelled"
TRAIN_STATUS_BOARDING = "Boarding"
TRAIN_STATUS_DEPARTED = "Departed"
TRAIN_STATUS_SCHEDULED = "Scheduled"

# Harlem Line fallback stop list (stop_id → name) used before GTFS loads
HARLEM_LINE_STATIONS = {
    "1": "Grand Central Terminal",
    "2": "Harlem-125th Street",
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
}

STATION_NAME_TO_ID = {v: k for k, v in HARLEM_LINE_STATIONS.items()}

# Alias used by config_flow and coordinator
FALLBACK_STATIONS = HARLEM_LINE_STATIONS
