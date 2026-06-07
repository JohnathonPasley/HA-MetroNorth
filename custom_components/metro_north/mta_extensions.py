"""Parse MTA Railroad custom GTFS-RT extensions from protobuf unknown fields.

The MTARR proto (gtfs-realtime-MTARR.proto) defines two extensions:

  extend TripUpdate.StopTimeUpdate { MtaRailroadStopTimeUpdate @ field 1005 }
    - track       (string, field 1)  — platform/track number
    - trainStatus (string, field 2)  — official MTA status string

  extend VehiclePosition.CarriageDetails { MtaRailroadCarriageDetails @ field 1005 }
    - bicycles_allowed (int32, field 1)
    - carriage_class   (string, field 2)
    - quiet_carriage   (enum,   field 3)
    - toilet_facilities(enum,   field 4)

Because the MTARR descriptor is not registered in the default protobuf pool,
these extension fields appear in .UnknownFields() on the parent message.
We parse the embedded message bytes directly from the wire format.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_MTA_EXT_FIELD = 1005  # extension field number on both StopTimeUpdate and CarriageDetails


def diagnose_stop_time_update(stu: object) -> dict:
    """Return diagnostic info about MTARR extension presence on a StopTimeUpdate."""
    result: dict = {
        "mtarr_extension_present": False,
        "unknown_field_numbers": [],
        "track": "",
        "train_status": "",
    }
    # Try UnknownFields() API first (pure Python / older protobuf)
    try:
        ufs = list(stu.UnknownFields())
        result["unknown_field_numbers"] = [uf.field_number for uf in ufs]
        for uf in ufs:
            if uf.field_number == _MTA_EXT_FIELD:
                result["mtarr_extension_present"] = True
                raw = uf.data
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    track, status = _parse_track_and_status(bytes(raw))
                    result["track"] = track
                    result["train_status"] = status
                return result
    except Exception:
        pass
    # Fallback: parse raw _unknown_fields bytes (protobuf 4.x upb C backend)
    try:
        raw_bytes = getattr(stu, "_unknown_fields", b"") or b""
        if raw_bytes:
            nums, track, status = _parse_raw_unknown_fields(bytes(raw_bytes))
            result["unknown_field_numbers"] = nums
            if track or status:
                result["mtarr_extension_present"] = True
                result["track"] = track
                result["train_status"] = status
    except Exception as err:
        result["error"] = str(err)
    return result


def extract_stop_time_update_ext(stu: object) -> tuple[str, str]:
    """Return (track, trainStatus) from MtaRailroadStopTimeUpdate on a StopTimeUpdate.

    Returns ("", "") when the extension is absent or unparseable.
    """
    # Try UnknownFields() API first (pure Python / older protobuf)
    try:
        for uf in stu.UnknownFields():
            if uf.field_number == _MTA_EXT_FIELD:
                raw = uf.data
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    return _parse_track_and_status(bytes(raw))
    except Exception:
        pass
    # Fallback: parse raw _unknown_fields bytes (protobuf 4.x upb C backend)
    try:
        raw_bytes = getattr(stu, "_unknown_fields", b"") or b""
        if raw_bytes:
            _, track, status = _parse_raw_unknown_fields(bytes(raw_bytes))
            if track or status:
                return track, status
    except Exception as err:
        _LOGGER.debug("MTARR StopTimeUpdate extension parse error: %s", err)
    return "", ""


def extract_carriage_details(carriage: object) -> dict[str, object]:
    """Return a dict with MtaRailroadCarriageDetails fields, or {} if absent."""
    result: dict[str, object] = {}
    try:
        for uf in carriage.UnknownFields():
            if uf.field_number == _MTA_EXT_FIELD:
                raw = uf.data
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    result = _parse_carriage(bytes(raw))
    except Exception as err:
        _LOGGER.debug("MTARR CarriageDetails extension parse error: %s", err)
    return result


# ── Wire-format parsers ────────────────────────────────────────────────────


def _parse_raw_unknown_fields(data: bytes) -> tuple[list[int], str, str]:
    """Parse a flat sequence of protobuf unknown fields stored in _unknown_fields.

    Returns (field_numbers_seen, track, train_status).
    Used as a fallback for protobuf 4.x upb backend where UnknownFields() is empty.
    """
    field_numbers: list[int] = []
    track = ""
    train_status = ""
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            field_numbers.append(field_num)
            if wire_type == 2:  # length-delimited
                length, pos = _varint(data, pos)
                value_bytes = data[pos: pos + length]
                pos += length
                if field_num == _MTA_EXT_FIELD:
                    t, s = _parse_track_and_status(bytes(value_bytes))
                    if t:
                        track = t
                    if s:
                        train_status = s
            elif wire_type == 0:
                _, pos = _varint(data, pos)
            elif wire_type == 1:
                pos += 8
            elif wire_type == 5:
                pos += 4
            else:
                break
        except Exception:
            break
    return field_numbers, track, train_status


def _parse_track_and_status(data: bytes) -> tuple[str, str]:
    """Parse MtaRailroadStopTimeUpdate: field 1 = track, field 2 = trainStatus."""
    track = ""
    train_status = ""
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 2:  # length-delimited (string / bytes)
                length, pos = _varint(data, pos)
                value = data[pos : pos + length].decode("utf-8", errors="replace")
                pos += length
                if field_num == 1:
                    track = value
                elif field_num == 2:
                    train_status = value
            else:
                pos = _skip(data, pos, wire_type)
        except Exception:
            break
    return track, train_status


def _parse_carriage(data: bytes) -> dict[str, object]:
    """Parse MtaRailroadCarriageDetails."""
    result: dict[str, object] = {}
    _QUIET = {0: "unknown", 1: "quiet", 2: "not_quiet"}
    _TOILET = {0: "unknown", 1: "present", 2: "absent"}
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 0:  # varint
                val, pos = _varint(data, pos)
                if field_num == 1:
                    result["bicycles_allowed"] = val
                elif field_num == 3:
                    result["quiet_carriage"] = _QUIET.get(val, str(val))
                elif field_num == 4:
                    result["toilet_facilities"] = _TOILET.get(val, str(val))
            elif wire_type == 2:
                length, pos = _varint(data, pos)
                value = data[pos : pos + length].decode("utf-8", errors="replace")
                pos += length
                if field_num == 2:
                    result["carriage_class"] = value
            else:
                pos = _skip(data, pos, wire_type)
        except Exception:
            break
    return result


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf varint; return (value, new_pos)."""
    result = shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _skip(data: bytes, pos: int, wire_type: int) -> int:
    """Advance pos past a field of the given wire type."""
    if wire_type == 0:  # varint
        while data[pos] & 0x80:
            pos += 1
        return pos + 1
    if wire_type == 1:  # 64-bit
        return pos + 8
    if wire_type == 2:  # length-delimited
        length, pos = _varint(data, pos)
        return pos + length
    if wire_type == 5:  # 32-bit
        return pos + 4
    raise ValueError(f"Unknown wire type {wire_type}")
