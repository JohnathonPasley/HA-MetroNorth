"""Parse MTA Railroad custom GTFS-RT extensions from protobuf unknown fields.

The MTARR proto (gtfs-realtime-MTARR.proto) defines two extensions:

  extend TripUpdate.StopTimeUpdate { MtaRailroadStopTimeUpdate @ field 1005 }
    - track       (string, field 1)  — platform/track number
    - trainStatus (string, field 2)  — official MTA status string

  extend VehiclePosition.CarriageDetails { MtaRailroadCarriageDetails @ field 1005 }
    - bicycles_allowed (int32,  field 1)
    - carriage_class   (string, field 2)
    - quiet_carriage   (enum,   field 3)  0=unknown, 1=quiet, 2=not_quiet
    - toilet_facilities(enum,   field 4)  0=unknown, 1=present, 2=absent

Because the MTARR descriptor is not registered in the default protobuf pool,
field 1005 appears as an unknown field on the parent message.

Access strategy (tried in order):
  1. google.protobuf.unknown_fields.UnknownFieldSet — correct for protobuf 4.x upb C backend
  2. message.UnknownFields()                        — pure-Python / older protobuf
  3. message._unknown_fields raw bytes              — last-resort for some builds

Note on track availability: the MTA Metro North feed only includes the track
extension on the Grand Central Terminal stop_time_update, and only after a
platform has been assigned (typically ~10 min before departure). For inbound
trains that haven't reached GCT the mtarr_raw attribute will legitimately be
empty.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_MTA_EXT_FIELD = 1005  # extension field number on both StopTimeUpdate and CarriageDetails
_MAX_STR_LEN = 256     # cap on any single string to prevent large allocations
_MAX_VARINT_SHIFT = 63 # protobuf varints are at most 64-bit


# ── Unified unknown-field accessor ────────────────────────────────────────────

def _get_mtarr_ext_bytes(message: object) -> bytes:
    """Return the raw embedded-message bytes of extension field 1005.

    Tries three backends so the code works across protobuf 3.x pure-Python,
    protobuf 4.x pure-Python, and protobuf 4.x upb C backend.
    """
    # 1. google.protobuf.unknown_fields.UnknownFieldSet
    #    This is the officially supported cross-backend API introduced in 3.12
    #    and the only one that reliably works with the upb C backend in 4.x.
    try:
        from google.protobuf import unknown_fields as _uf_mod
        for uf in _uf_mod.UnknownFieldSet(message):
            if uf.field_number == _MTA_EXT_FIELD:
                data = uf.data
                if isinstance(data, (bytes, bytearray, memoryview)):
                    return bytes(data)
    except Exception:
        pass

    # 2. message.UnknownFields() — works with pure-Python protobuf (< 4.x or env flag)
    try:
        for uf in message.UnknownFields():
            if uf.field_number == _MTA_EXT_FIELD:
                data = uf.data
                if isinstance(data, (bytes, bytearray, memoryview)):
                    return bytes(data)
    except Exception:
        pass

    # 3. _unknown_fields raw bytes — present in some protobuf builds as a bytes blob
    try:
        raw = getattr(message, "_unknown_fields", b"") or b""
        if raw:
            return _extract_field_bytes(bytes(raw), _MTA_EXT_FIELD)
    except Exception:
        pass

    return b""


# ── Public extractors ─────────────────────────────────────────────────────────

def extract_stop_time_update_ext(stu: object) -> tuple[str, str]:
    """Return (track, trainStatus) from MtaRailroadStopTimeUpdate.

    Returns ("", "") when the extension is absent or unparseable.
    """
    raw = _get_mtarr_ext_bytes(stu)
    if raw:
        return _parse_track_and_status(raw)
    return "", ""


def extract_stop_time_update_ext_debug(stu: object) -> tuple[str, str, str]:
    """Return (track, trainStatus, raw_hex) from MtaRailroadStopTimeUpdate.

    raw_hex is the hex encoding of the MTARR extension's embedded message bytes;
    empty string when the extension is absent.
    """
    raw = _get_mtarr_ext_bytes(stu)
    if raw:
        track, status = _parse_track_and_status(raw)
        return track, status, raw.hex()
    return "", "", ""


def extract_carriage_details(carriage: object) -> dict[str, object]:
    """Return a dict with MtaRailroadCarriageDetails fields, or {} if absent."""
    raw = _get_mtarr_ext_bytes(carriage)
    if raw:
        return _parse_carriage(raw)
    return {}


# ── Wire-format parsers ────────────────────────────────────────────────────────

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
                if length < 0 or pos + length > n:
                    break
                raw = data[pos: pos + length]
                pos += length
                value = raw[:_MAX_STR_LEN].decode("utf-8", errors="replace")
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
            elif wire_type == 2:  # length-delimited
                length, pos = _varint(data, pos)
                if length < 0 or pos + length > n:
                    break
                value = data[pos: pos + length][:_MAX_STR_LEN].decode("utf-8", errors="replace")
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
        if pos >= len(data):
            raise ValueError("Truncated varint")
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > _MAX_VARINT_SHIFT:
            raise ValueError("Varint overflow")


def _extract_field_bytes(data: bytes, target_field: int) -> bytes:
    """Return raw bytes of the first length-delimited field matching target_field."""
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 2:
                length, pos = _varint(data, pos)
                if length < 0 or pos + length > n:
                    break
                value = data[pos: pos + length]
                pos += length
                if field_num == target_field:
                    return value
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
    return b""


def _skip(data: bytes, pos: int, wire_type: int) -> int:
    """Advance pos past a field of the given wire type."""
    if wire_type == 0:
        while data[pos] & 0x80:
            pos += 1
        return pos + 1
    if wire_type == 1:
        return pos + 8
    if wire_type == 2:
        length, pos = _varint(data, pos)
        return pos + length
    if wire_type == 5:
        return pos + 4
    raise ValueError(f"Unknown wire type {wire_type}")
