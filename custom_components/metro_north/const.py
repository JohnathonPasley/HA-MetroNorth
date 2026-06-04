"""Constants for MTA Metro North integration."""

DOMAIN = "metro_north"

CONF_API_KEY = "api_key"
CONF_STATIONS = "stations"
CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_UPDATE_INTERVAL = 30  # seconds
MIN_UPDATE_INTERVAL = 15
MAX_UPDATE_INTERVAL = 300

GTFS_RT_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr"
GTFS_RT_VEHICLES_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr-vehicles"

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

# Reverse lookup: name -> stop_id
STATION_NAME_TO_ID = {v: k for k, v in HARLEM_LINE_STATIONS.items()}

TRAIN_STATUS_ON_TIME = "On Time"
TRAIN_STATUS_DELAYED = "Delayed"
TRAIN_STATUS_CANCELLED = "Cancelled"
TRAIN_STATUS_BOARDING = "Boarding"
TRAIN_STATUS_DEPARTED = "Departed"
TRAIN_STATUS_SCHEDULED = "Scheduled"
