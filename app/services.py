"""Small functions that turn raw portal JSON into our clean API shapes.
Rule: never invent data. Missing/invalid -> None."""
from datetime import datetime
import math
import re

from app.devalue import unflatten

TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M"   # observed: DD/MM/YYYY HH:mm  (timezone NOT established)


def to_float(value):
    """'17717.54' -> 17717.54 ; None / '' / 'abc' / NaN -> None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_int(value):
    number = to_float(value)
    return int(number) if number is not None and number == int(number) else None


def parse_timestamp(text):
    """Parse explicitly (never rely on a generic date parser). Returns datetime or None."""
    try:
        return datetime.strptime(str(text).strip(), TIMESTAMP_FORMAT)
    except ValueError:
        return None


# ---------------- meters ----------------
def normalize_meter(raw):
    return {
        "meterId": raw.get("meterId"),
        "serialNo": raw.get("serialNo"),
        "make": raw.get("make"),
        "phaseType": raw.get("phaseType"),
        # the detail page showed "Installed"; the sample search JSON used "installStatus"
        "installationStatus": raw.get("installationStatus", raw.get("installStatus")),
        "installationType": raw.get("installationType"),
        "dtCode": raw.get("dtCode"),
        # Hierarchy API representation is NOT verified. Passed through only if upstream supplies it.
        "hierarchy": raw.get("hierarchy") if isinstance(raw.get("hierarchy"), (list, dict)) else None,
    }


def normalize_page(payload, requested_page, normalize_item):
    """Shared for meter search and transformers. Pagination values only if upstream gave valid ints."""
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("missing data array")
    return {
        "data": [normalize_item(i) for i in items if isinstance(i, dict)],
        "total": to_int(payload.get("total")),
        "page": to_int(payload.get("page")) or requested_page,
        "pageSize": to_int(payload.get("pageSize")),
    }


# ---------------- transformers ----------------
def normalize_transformer(raw):
    return {
        "code": raw.get("code"),
        "name": raw.get("name"),
        "feederCode": raw.get("feederCode"),
        "capacityKva": to_float(raw.get("capacityKva")),  # RATED capacity, not load
    }


# ---------------- geo ----------------
def normalize_geo(payload):
    """Return {'latitude','longitude'} floats, or None if missing/invalid/out of range."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    lat, lon = to_float(data.get("latitude")), to_float(data.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    return {"latitude": lat, "longitude": lon}


# ---------------- energy ----------------
def normalize_energy(payload):
    """kwh/kvah are CUMULATIVE registers. intervalKwh is the difference from the previous reading."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("missing data array")

    warnings = {"skippedInvalidTimestamp": 0, "duplicateTimestamps": 0, "registerDecreases": 0}
    by_time = {}
    for raw in rows:
        if not isinstance(raw, dict):
            warnings["skippedInvalidTimestamp"] += 1
            continue
        ts = parse_timestamp(raw.get("timestamp"))
        if ts is None:
            warnings["skippedInvalidTimestamp"] += 1
            continue
        if ts in by_time:
            warnings["duplicateTimestamps"] += 1   # keep the last one seen
        by_time[ts] = {
            "timestamp": ts.isoformat(),  # naive local time as reported; timezone unknown
            "kwh": to_float(raw.get("kwh")),
            "kvah": to_float(raw.get("kvah")),
            "voltR": to_float(raw.get("voltR")),
            "intervalKwh": None,
        }

    readings = [by_time[t] for t in sorted(by_time)]
    previous_kwh = None
    for r in readings:
        if r["kwh"] is not None and previous_kwh is not None:
            diff = r["kwh"] - previous_kwh
            if diff < 0:
                warnings["registerDecreases"] += 1   # reset/rollover/correction: don't report negative usage
            else:
                r["intervalKwh"] = round(diff, 3)
        if r["kwh"] is not None:
            previous_kwh = r["kwh"]
    return {"data": readings, "count": len(readings), "warnings": warnings}


# ---------------- meter profile (installation type + network hierarchy) ----------------
# Observed on the portal's meter page (/meters/<id>/__data.json). Order = top of the network down.
HIERARCHY_LEVELS = [("Zone", "zone"), ("Circle", "circle"), ("Division", "division"),
                    ("Subdivision", "subdivision"), ("Sub Station", "substation"),
                    ("Feeder", "feeder"), ("DT", "transformer")]
_LABEL = re.compile(r"^(.*?)\s*\(([^()]+)\)\s*$")  # "Jaipur Zone 3 (Z-03)" -> name, code


def clean_text(value):
    """Non-empty stripped string, otherwise None (never invents a value)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_level_label(text):
    """'Feeder 3 (F-003)' -> ('Feeder 3', 'F-003'). No '(code)' part -> (text, None)."""
    text = clean_text(text)
    if text is None:
        return None, None
    match = _LABEL.match(text)
    return (match.group(1).strip() or None, match.group(2).strip()) if match else (text, None)


class MeterNotFound(LookupError):
    """The portal's page data for this meter is an error node saying 404 / not found."""


def _is_not_found(node):
    """SvelteKit reports a failed page load as an 'error' node (still HTTP 200)."""
    if not isinstance(node, dict) or node.get("type") != "error":
        return False
    error = node.get("error")
    message = error.get("message") if isinstance(error, dict) else error
    return node.get("status") == 404 or (isinstance(message, str) and "not found" in message.lower())


def _detail_lookup(page):
    """The page data also has `detail`: rows of {parameterName, parameterValue}. -> {lowercase name: value}."""
    rows, found = page.get("detail"), {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and clean_text(row.get("parameterName")):
                found[row["parameterName"].strip().casefold()] = row.get("parameterValue")
    return found


def normalize_meter_profile(payload, meter_id):
    """Decode the SvelteKit page data of one meter.
    Raises MeterNotFound for a 404 error node and ValueError for any other unexpected shape."""
    nodes = payload.get("nodes") if isinstance(payload, dict) else None
    if not isinstance(nodes, list):
        raise ValueError("no nodes")
    if any(_is_not_found(node) for node in nodes):
        raise MeterNotFound(meter_id)
    page = None
    for node in nodes:
        if isinstance(node, dict) and node.get("type") == "data" and isinstance(node.get("data"), list) and node["data"]:
            decoded = unflatten(node["data"])
            if isinstance(decoded, dict) and "hierarchy" in decoded:
                page = decoded
                break
    if page is None or not isinstance(page.get("hierarchy"), dict) or str(page.get("meterId", "")).casefold() != meter_id.casefold():
        raise ValueError("unexpected meter page data")
    raw = page["hierarchy"]
    detail = _detail_lookup(page)
    levels = []
    for label, key in HIERARCHY_LEVELS:
        name, code = parse_level_label(raw.get(label))
        if name is not None:  # absent or empty levels are left out, not invented
            levels.append({"level": key, "name": name, "code": code})
    return {
        "meterId": str(page["meterId"]),
        # Looked up in the hierarchy object first, then in the nameplate `detail` rows.
        "installationStatus": clean_text(raw.get("Installation Status")) or clean_text(detail.get("installation status")),
        "installationType": clean_text(raw.get("Installation Type")) or clean_text(detail.get("installation type")),
        "hierarchy": levels,
        "hierarchyComplete": len(levels) == len(HIERARCHY_LEVELS),
    }