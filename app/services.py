"""Small functions that turn raw portal JSON into our clean API shapes.
Rule: never invent data. Missing/invalid -> None."""
from datetime import datetime
import math

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
