"""Parse Mercury GTFS-RT service alert extensions from protobuf unknown fields.

The Mercury proto (gtfs-realtime-service-status.proto) defines:

  extend google.transit.realtime.Alert {
    MercuryAlert mercury_alert = 1001;
  }
    - created_at               (uint64, field 1)
    - updated_at               (uint64, field 2)
    - alert_type               (string, field 3)
    - station_alternative      (repeated embedded, field 4)
    - display_before_active    (uint64, field 7)
    - human_readable_active_period (embedded TranslatedString, field 8)

  extend google.transit.realtime.EntitySelector {
    MercuryEntitySelector mercury_entity_selector = 1001;
  }
    - sort_order (string, field 1) — "GTFS-ID:Priority"

The extension fields appear in .UnknownFields() on the parent messages.
"""
from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)

_MERCURY_FIELD = 1001  # extension field number on both Alert and EntitySelector


def extract_mercury_alert(alert: object) -> dict:
    """Return parsed MercuryAlert fields from an Alert's unknown fields, or {}."""
    try:
        for uf in alert.UnknownFields():
            if uf.field_number == _MERCURY_FIELD:
                raw = uf.data
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    return _parse_mercury_alert(bytes(raw))
    except Exception as err:
        _LOGGER.debug("Mercury Alert extension parse error: %s", err)
    return {}


def extract_mercury_entity_selector(entity_selector: object) -> dict:
    """Return parsed MercuryEntitySelector fields, or {}."""
    try:
        for uf in entity_selector.UnknownFields():
            if uf.field_number == _MERCURY_FIELD:
                raw = uf.data
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    return _parse_mercury_entity_selector(bytes(raw))
    except Exception as err:
        _LOGGER.debug("Mercury EntitySelector extension parse error: %s", err)
    return {}


def get_translated_text(translated_string: object, lang: str = "en") -> str:
    """Extract the best translation from a GTFS-RT TranslatedString."""
    try:
        best = ""
        for t in translated_string.translation:
            if t.language == lang:
                return t.text
            if not best:
                best = t.text
        return best
    except Exception:
        return ""


# ── Wire-format parsers ────────────────────────────────────────────────────


def _parse_mercury_alert(data: bytes) -> dict:
    """Parse MercuryAlert embedded message bytes."""
    result: dict = {}
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 0:  # varint (uint64)
                val, pos = _varint(data, pos)
                if field_num == 1:
                    result["created_at"] = val
                elif field_num == 2:
                    result["updated_at"] = val
                elif field_num == 7:
                    result["display_before_active"] = val
            elif wire_type == 2:  # length-delimited (string or embedded)
                length, pos = _varint(data, pos)
                raw = data[pos: pos + length]
                pos += length
                if field_num == 3:
                    result["alert_type"] = raw.decode("utf-8", errors="replace")
                elif field_num == 8:
                    # human_readable_active_period: MercuryTranslatedString
                    # field 1 = repeated Translation (embedded: field 1=text, field 2=language)
                    result["human_readable_active_period"] = _parse_translated_string(raw)
            else:
                pos = _skip(data, pos, wire_type)
        except Exception:
            break
    return result


def _parse_translated_string(data: bytes) -> str:
    """Parse a Mercury TranslatedString embedded message; return English text or first."""
    translations: list[tuple[str, str]] = []  # [(language, text)]
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 2 and field_num == 1:
                # Repeated Translation message: field 1=text(str), field 2=language(str)
                length, pos = _varint(data, pos)
                raw = data[pos: pos + length]
                pos += length
                text, lang = _parse_translation(raw)
                translations.append((lang, text))
            else:
                pos = _skip(data, pos, wire_type)
        except Exception:
            break
    for lang, text in translations:
        if lang == "en":
            return text
    return translations[0][1] if translations else ""


def _parse_translation(data: bytes) -> tuple[str, str]:
    """Parse a Translation message: field 1=text, field 2=language."""
    text = ""
    lang = ""
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 2:
                length, pos = _varint(data, pos)
                value = data[pos: pos + length].decode("utf-8", errors="replace")
                pos += length
                if field_num == 1:
                    text = value
                elif field_num == 2:
                    lang = value
            else:
                pos = _skip(data, pos, wire_type)
        except Exception:
            break
    return text, lang


def _parse_mercury_entity_selector(data: bytes) -> dict:
    """Parse MercuryEntitySelector: field 1 = sort_order string."""
    result: dict = {}
    pos = 0
    n = len(data)
    while pos < n:
        try:
            tag, pos = _varint(data, pos)
            field_num = tag >> 3
            wire_type = tag & 0x7
            if wire_type == 2 and field_num == 1:
                length, pos = _varint(data, pos)
                result["sort_order"] = data[pos: pos + length].decode("utf-8", errors="replace")
                pos += length
            else:
                pos = _skip(data, pos, wire_type)
        except Exception:
            break
    return result


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _skip(data: bytes, pos: int, wire_type: int) -> int:
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
