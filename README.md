# MTA Metro North — Home Assistant Integration

A custom Home Assistant integration that delivers real-time train data for MTA Metro North Railroad stations via the MTA GTFS-Realtime (GTFS-RT) feed.

---

## Features

- **Station sensors** — monitor one or more Metro North stations simultaneously
- **Individual train sensors (Train 1–N)** — dedicated sensors for each upcoming train slot; the state is the estimated departure time
- **Direction filter** — show all trains, inbound only (toward Grand Central), or outbound only (away from Grand Central)
- **Route filter** — limit sensors to a specific line (Harlem, Hudson, or New Haven)
- **Service type classification** — trains are automatically classified as Local, Express, or Super Express based on their stop pattern
- **En-route positioning** — sensors report the train's current or next stop as it moves between stations
- **stops_to_station** — attribute reporting how many stops remain until the monitored station
- **Departure status sensor** — human-readable departure status (e.g., "Departing", "Scheduled to Depart Soon", "Scheduled Departure", or "Running N min Early")
- **Station zones** — each monitored station creates a Home Assistant zone at the station's coordinates
- **Service alerts** — active MTA service alerts are surfaced on affected station and route sensors
- **Upcoming Trains sensor** — reports the count of upcoming trains and includes a full attribute list of all departures
- **Vehicle map trackers** — GPS device trackers for active trains, pinned to the current stop and displayed on the Home Assistant map with line-colored icons; can be toggled on/off in Options
- **Auto-cleanup of stale trains** — trackers for trains that are no longer active are automatically removed
- **Line-colored map pins** — Harlem Line trains appear in blue, Hudson Line in green, New Haven Line in red
- **Peak / off-peak adaptive polling** — faster refresh during configurable morning and evening peak windows, slower polling at all other times

---

## Installation

1. Make sure [HACS](https://hacs.xyz/) is installed in your Home Assistant instance.
2. In Home Assistant, go to **HACS → Integrations**.
3. Click the three-dot menu in the top-right corner and choose **Custom repositories**.
4. Add the GitHub URL for this repository and set the category to **Integration**.
5. Click **Add**, then search for **Metro North** and install it.
6. Restart Home Assistant.
7. Go to **Settings → Devices & Services → Add Integration** and search for **Metro North**.

---

## Configuration

Setup happens in two steps.

### Step 1 — Stations, Direction, and Train Count

| Field | Description |
|---|---|
| **Stations to monitor** | Select one or more Metro North stations from the dropdown. Station names are loaded from the MTA static GTFS feed; a built-in fallback list is used if the feed is unavailable at setup time. |
| **Train direction** | Filter trains by direction: *Both directions*, *Inbound only (toward Grand Central)*, or *Outbound only (from Grand Central)*. |
| **Individual train sensors per station (1–20)** | How many numbered Train sensors to create per station (Train 1, Train 2, …). Each sensor's state is the estimated departure time for that departure slot. |
| **Route filter** | Optionally restrict sensors to one or more lines (Harlem, Hudson, New Haven). Leave empty to show all lines. |

### Step 2 — Poll Schedule

| Field | Description |
|---|---|
| **Peak window 1 — start / end** | Start and end time for the morning peak window (default 07:00–09:00). |
| **Peak window 1 — interval (s)** | Refresh interval in seconds during the morning peak (default 30 s). |
| **Peak window 2 — start / end** | Start and end time for the evening peak window (default 17:00–20:00). |
| **Peak window 2 — interval (s)** | Refresh interval in seconds during the evening peak (default 30 s). |
| **Off-peak interval (s)** | Refresh interval in seconds outside of peak windows (default 120 s). |

All settings can be changed after initial setup via **Settings → Devices & Services → Metro North → Configure**.

---

## Entities

### Train N Sensors

One sensor is created per train slot per station (e.g., *Metro North White Plains Inbound Train 1*).

- **State** — estimated departure time in 12-hour format (e.g., `8:14 AM`), or unavailable if no train occupies that slot.
- **Attributes** — see the Sensor Attributes section below.

### Upcoming Trains Sensor

One sensor per station (e.g., *Metro North White Plains Inbound Upcoming Trains*).

- **State** — integer count of upcoming trains (up to 10).
- **Attributes** — `upcoming_trains`: a list of attribute dictionaries (same schema as a Train N sensor) for each upcoming departure.

### Vehicle Trackers

One device tracker per active train vehicle detected in the MTA GTFS-RT vehicles feed.

- **State** — `home` (active) or `not_home` (stale / removed).
- **Attributes** — latitude, longitude, speed, bearing, occupancy status, and train/trip details.
- Tracker entities are automatically removed when a train is no longer active in the feed.
- Vehicle trackers can be disabled entirely via the **Show vehicle trackers** toggle in Options.

---

## Sensor Attributes

| Attribute | Description |
|---|---|
| `train_number` | GTFS trip ID (used as the train identifier) |
| `track` | Scheduled or assigned track number |
| `scheduled_time` | Originally scheduled departure time |
| `estimated_time` | Real-time estimated departure time |
| `delay_minutes` | Delay in minutes (negative = early) |
| `status` | Human-readable status: On Time, Delayed, Cancelled, Boarding, Departed, or Scheduled |
| `origin` | First stop / originating station for this trip |
| `destination` | Final stop / terminal station for this trip |
| `headsign` | Displayed headsign for the trip |
| `line` | Route name (e.g., Harlem, Hudson, New Haven) |
| `direction` | `Inbound` (toward Grand Central) or `Outbound` (away from Grand Central) |
| `service_type` | Service classification: `Local`, `Express`, or `Super Express` |
| `current_stop` | Name of the stop the train is currently at or departing from (e.g., `En Route to White Plains`) |
| `next_stop` | Name of the next scheduled stop |
| `stops_remaining` | Number of stops remaining in the trip |
| `stops_to_station` | Number of stops between the train's current position and this monitored station |
| `departure_status` | Human-readable departure status (e.g., `Scheduled Departure`, `Scheduled to Depart Soon`, `Departing`, `Running N min Early`) |
| `trip_stops` | List of all stops on the trip with `stop_name`, `arrival`, and `departure` times (up to 50 stops) |

---

## Map

Vehicle trackers appear on the Home Assistant map with colored pins by line:

| Line | Pin color |
|---|---|
| Harlem Line | Blue |
| Hudson Line | Green |
| New Haven Line | Red |

> **Note:** The MTA Metro North GTFS-RT feed does not publish live GPS coordinates for moving trains. Vehicle positions are pinned to the train's most recently reported stop, not to a real-time location between stations. This matches the behavior of official MTA departure boards.

---

## Polling Schedule

The integration uses a smart polling schedule to balance freshness against API load:

- During **peak window 1** (default 07:00–09:00) and **peak window 2** (default 17:00–20:00), data refreshes every **30 seconds**.
- Outside peak windows, data refreshes every **120 seconds**.
- All window times and intervals are fully configurable per the Configuration section above.

---

## Lines Supported

The integration supports all Metro North Railroad stations across all three main lines:

- **Harlem Line** — Grand Central Terminal to Wassaic
- **Hudson Line** — Grand Central Terminal to Poughkeepsie
- **New Haven Line** — Grand Central Terminal to New Haven, with New Canaan, Danbury, and Waterbury branches

Station names are sourced from the MTA official GTFS static data, so the full station list stays current with any MTA schedule changes.

### API Key

No API key is required. As of 2024, the MTA removed the API key requirement for all real-time GTFS feeds. The integration connects directly to the feed with no registration or authentication.

---

> This integration was developed with [Claude](https://claude.ai) (Anthropic).
