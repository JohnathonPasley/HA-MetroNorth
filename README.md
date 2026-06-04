# HA-MetroNorth

A Home Assistant custom integration for **MTA Metro North Railroad** (Harlem Line) that provides real-time train data via the MTA GTFS-RT feed.

## Features

- **Next Train sensor** — shows the next departing train from your chosen station with track number, scheduled/estimated time, and delay status
- **Upcoming Trains sensor** — lists the next 10 trains with full details
- **Train Vehicle Trackers** — active trains appear as GPS device trackers on the HA map with bearing and speed
- **Harlem Line stations** — all 34 stations from Grand Central Terminal to Wassaic

## Installation

### HACS (recommended)

1. In HACS → Integrations → ⋮ → Custom Repositories
2. Add `https://github.com/johnathonpasley/ha-metronorth` as type **Integration**
3. Install **MTA Metro North**
4. Restart Home Assistant

### Manual

1. Copy `custom_components/metro_north/` into your HA `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **MTA Metro North**
3. Select your home station from the Harlem Line dropdown
4. Optionally enter your MTA API key (free — register at https://api.mta.info)
5. Set the refresh interval (default 30 s, minimum 15 s)

### API Key

The MTA feed at `https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/mnr%2Fgtfs-mnr` works without a key for casual polling, but the MTA recommends registering for a free API key to avoid rate limiting. Pass it in as an `x-api-key` header via the integration config.

## Entities Created

For each configured station the integration creates:

| Entity | Type | Description |
|---|---|---|
| `sensor.metro_north_<station>_next_train` | Sensor | Next departure time |
| `sensor.metro_north_<station>_upcoming_trains` | Sensor | Count + list of next 10 trains |

For each active train vehicle on the line:

| Entity | Type | Description |
|---|---|---|
| `device_tracker.metro_north_train_<id>` | Device Tracker | GPS position on map |

## Sensor Attributes (Next Train)

```
train_number       Trip ID
track              Platform/track number
scheduled_time     Published departure time
estimated_time     Real-time estimated departure
delay_minutes      Minutes late (0 = on time)
status             On Time / Delayed
destination        End terminus
origin             Starting station
line               Harlem
direction          Inbound / Outbound
```

## Stop ID Notes

The Harlem Line stop IDs in `const.py` are based on the MTA GTFS static feed. If trains aren't appearing for a station, verify the stop IDs against the latest static GTFS ZIP from http://web.mta.info/developers/data/mnr/google_transit.zip.
