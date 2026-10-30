"""FastAPI app: routes, validation, error mapping. Talks to the portal only via PortalClient."""
from functools import lru_cache
from pathlib import Path as FsPath
from typing import Optional

from fastapi import Depends, FastAPI, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import services
from app.portal_client import PortalClient, PortalError

app = FastAPI(
    title="Urja Meter Service",
    version="1.0.0",
    description="A clean read-only API over data observed in the Urja Meter Ops portal. "
                "It is NOT an official portal API. See PROTOCOL.md for what was verified.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # the local dashboard only
    allow_methods=["GET"],
    allow_headers=["*"],
)

METER_ID = Path(..., pattern=r"^[A-Za-z0-9_-]{1,32}$", description="Meter id, e.g. J100020", examples=["J100020"])


# ---------- response models (these feed openapi.json) ----------
class Meter(BaseModel):
    meterId: Optional[str] = None
    serialNo: Optional[str] = None
    make: Optional[str] = None
    phaseType: Optional[str] = None
    installationStatus: Optional[str] = None
    installationType: Optional[str] = None
    dtCode: Optional[str] = None
    hierarchy: Optional[list | dict] = None  # always null here: search results lack it; see /meters/{id}/hierarchy


class MeterPage(BaseModel):
    data: list[Meter]
    total: Optional[int] = None
    page: int
    pageSize: Optional[int] = None


class HierarchyLevel(BaseModel):
    level: str  # zone, circle, division, subdivision, substation, feeder, transformer
    name: Optional[str] = None
    code: Optional[str] = None  # e.g. "Z-03"; null if the portal label had no "(code)"


class MeterProfile(BaseModel):
    meterId: str
    installationStatus: Optional[str] = None
    installationType: Optional[str] = None  # e.g. "CT Operated"
    hierarchy: list[HierarchyLevel]  # top of the network down; missing levels are omitted
    hierarchyComplete: bool  # true only when all 7 levels were present


class Reading(BaseModel):
    timestamp: str  # ISO-8601, no timezone (portal timezone not established)
    kwh: Optional[float] = None  # cumulative register
    kvah: Optional[float] = None  # cumulative register
    voltR: Optional[float] = None  # meaning/units not established
    intervalKwh: Optional[float] = None  # difference from previous reading; null if unknown/negative


class EnergyWarnings(BaseModel):
    skippedInvalidTimestamp: int
    duplicateTimestamps: int
    registerDecreases: int


class EnergyResponse(BaseModel):
    data: list[Reading]
    count: int
    warnings: EnergyWarnings


class Geo(BaseModel):
    latitude: float
    longitude: float


class GeoResponse(BaseModel):
    data: Geo


class Transformer(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    feederCode: Optional[str] = None
    capacityKva: Optional[float] = None  # RATED capacity, not load


class TransformerPage(BaseModel):
    data: list[Transformer]
    total: Optional[int] = None
    page: int
    pageSize: Optional[int] = None


class ErrorResponse(BaseModel):
    error: str
    message: str


ERRORS = {
    404: {"model": ErrorResponse, "description": "Resource not found"},
    429: {"model": ErrorResponse, "description": "Upstream portal is rate-limiting (see Retry-After header)"},
    502: {"model": ErrorResponse, "description": "Upstream portal failed or returned unexpected data"},
    504: {"model": ErrorResponse, "description": "Upstream portal timed out"},
}


# ---------- dependency + error handling ----------
@lru_cache
def _client():
    return PortalClient()


def get_client():
    return _client()


def error(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={"error": code, "message": message}, headers=headers)


@app.exception_handler(PortalError)
def portal_error_handler(request, exc: PortalError):
    if exc.kind == "rate_limited":
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return error(429, "upstream_rate_limited",
                     "The upstream portal is temporarily rate-limiting requests. Please wait before trying again.", headers)
    if exc.kind == "timeout":
        return error(504, "upstream_timeout", exc.message)
    if exc.kind == "not_found":
        return error(404, "not_found", "The portal has no such resource.")
    return error(502, "upstream_error", exc.message)  # message is ours; no traces or secrets


def bad_payload():
    return error(502, "unexpected_upstream_data", "The portal returned data in an unexpected shape.")


# ---------- routes ----------
@app.get("/health", summary="Local API liveness")
def health():
    """Only says OUR process is running. It does not check the upstream portal."""
    return {"status": "ok", "service": "urja-meter-service", "upstream": "not checked"}


@app.get("/api/v1/meters", response_model=MeterPage, responses=ERRORS, summary="Search meters")
def list_meters(q: str = Query("", max_length=64, description="Search text (portal 'q' parameter)"),
                page: int = Query(1, ge=1, le=100000), client: PortalClient = Depends(get_client)):
    try:
        return services.normalize_page(client.search_meters(q, page), page, services.normalize_meter)
    except ValueError:
        return bad_payload()


@app.get("/api/v1/meters/{meter_id}", response_model=Meter, responses=ERRORS, summary="Meter details")
def meter_detail(meter_id: str = METER_ID, client: PortalClient = Depends(get_client)):
    """No separate details endpoint was observed, so this searches by id and requires an exact match."""
    try:
        page = services.normalize_page(client.search_meters(meter_id, 1), 1, services.normalize_meter)
    except ValueError:
        return bad_payload()
    for meter in page["data"]:
        if meter["meterId"] == meter_id:
            return meter
    return error(404, "meter_not_found", f"Meter {meter_id} was not found.")


@app.get("/api/v1/meters/{meter_id}/energy", response_model=EnergyResponse, responses=ERRORS,
         summary="Energy readings for ONE meter")
def meter_energy(meter_id: str = METER_ID, client: PortalClient = Depends(get_client)):
    """kwh/kvah are cumulative registers; intervalKwh is derived from consecutive kwh values."""
    try:
        return services.normalize_energy(client.get_energy(meter_id))
    except ValueError:
        return bad_payload()


@app.get("/api/v1/meters/{meter_id}/geo", response_model=GeoResponse, responses=ERRORS, summary="Meter coordinates")
def meter_geo(meter_id: str = METER_ID, client: PortalClient = Depends(get_client)):
    geo = services.normalize_geo(client.get_geo(meter_id))
    if geo is None:
        return error(404, "geo_unavailable", "No valid coordinates are available for this meter.")
    return {"data": geo}


@app.get("/api/v1/meters/{meter_id}/hierarchy", response_model=MeterProfile, responses=ERRORS,
         summary="Installation type and network hierarchy for ONE meter")
def meter_hierarchy(meter_id: str = METER_ID, client: PortalClient = Depends(get_client)):
    """Read from the portal's own meter page data (/meters/{id}/__data.json), which the search results lack."""
    try:
        return services.normalize_meter_profile(client.get_meter_page(meter_id), meter_id)
    except services.MeterNotFound:
        return error(404, "meter_not_found", f"Meter {meter_id} was not found.")
    except ValueError:
        return bad_payload()


@app.get("/api/v1/distribution-transformers", response_model=TransformerPage, responses=ERRORS,
         summary="Distribution transformers (rated capacity only)")
def transformers(page: int = Query(1, ge=1, le=100000), client: PortalClient = Depends(get_client)):
    try:
        return services.normalize_page(client.get_transformers(page), page, services.normalize_transformer)
    except ValueError:
        return bad_payload()


# Serve the dashboard from the same origin as the API (no CORS needed).
# IMPORTANT: keep this mount LAST so the /api/... and /health routes match first.
FRONTEND_DIR = FsPath(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")