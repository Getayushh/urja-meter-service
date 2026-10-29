"""All tests use a FAKE session. No live portal requests are ever made. Fixtures are MOCKED samples."""
import pytest
import requests
from fastapi.testclient import TestClient

from app import portal_client, services
from app.main import app, get_client
from app.portal_client import PortalClient

METER = {"meterId": "J100020", "serialNo": "AL20290", "make": "Allied", "phaseType": "three",
         "installStatus": "Installed", "dtCode": "DT-021"}
ENERGY = {"data": [
    {"timestamp": "24/06/2026 00:00", "kwh": "17718.07", "kvah": "19135.52", "voltR": "223"},
    {"timestamp": "23/06/2026 23:30", "kwh": "17717.54", "kvah": "19134.95", "voltR": "220"},  # out of order
    {"timestamp": "24/06/2026 00:30", "kwh": "17718.6", "kvah": "19136.08", "voltR": "225"},
    {"timestamp": "24/06/2026 01:30", "kwh": "17719.65", "kvah": "19137.22"},  # voltR missing
]}
DTS = {"data": [{"code": "DT-001", "name": "Malviya Nagar DT 1", "feederCode": "F-001", "capacityKva": 100},
                {"code": "DT-002", "name": "Mansarovar DT 2", "feederCode": "F-002", "capacityKva": 63}],
       "total": 40, "page": 1, "pageSize": 20}


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Returns queued responses (or raises queued exceptions) and counts calls."""
    def __init__(self, *items):
        self.items, self.calls = list(items), 0

    def request(self, method, url, params=None, headers=None, timeout=None, data=None):
        self.calls += 1
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(portal_client.time, "sleep", lambda s: None)  # tests must be fast


def api(*items):
    session = FakeSession(*items)
    app.dependency_overrides[get_client] = lambda: PortalClient(session=session, base_url="http://fake")
    return TestClient(app), session


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["upstream"] == "not checked"


def test_meter_listing():
    c, _ = api(FakeResponse(body={"data": [METER], "total": 10, "page": 1, "pageSize": 20}))
    body = c.get("/api/v1/meters?q=J1&page=1").json()
    assert body["total"] == 10 and body["pageSize"] == 20
    assert body["data"][0]["installationStatus"] == "Installed"
    assert body["data"][0]["hierarchy"] is None  # never invented


def test_empty_search_and_unknown_total():
    c, _ = api(FakeResponse(body={"data": []}))
    body = c.get("/api/v1/meters?q=zzz").json()
    assert body == {"data": [], "total": None, "page": 1, "pageSize": None}


def test_meter_not_found():
    c, _ = api(FakeResponse(body={"data": [METER]}))
    r = c.get("/api/v1/meters/NOPE1")
    assert r.status_code == 404 and r.json()["error"] == "meter_not_found"


def test_meter_found_by_exact_id():
    c, _ = api(FakeResponse(body={"data": [METER]}))
    assert c.get("/api/v1/meters/J100020").json()["serialNo"] == "AL20290"


def test_energy_normalization_sorting_and_interval():
    c, _ = api(FakeResponse(body=ENERGY))
    body = c.get("/api/v1/meters/J100020/energy").json()
    stamps = [r["timestamp"] for r in body["data"]]
    assert stamps == sorted(stamps) and stamps[0] == "2026-06-23T23:30:00"
    assert body["data"][0]["kwh"] == 17717.54 and body["data"][0]["intervalKwh"] is None  # first has no previous
    assert body["data"][1]["intervalKwh"] == 0.53


def test_missing_energy_field_stays_null():
    c, _ = api(FakeResponse(body=ENERGY))
    assert c.get("/api/v1/meters/J100020/energy").json()["data"][-1]["voltR"] is None


def test_invalid_values_and_duplicates_and_register_decrease():
    payload = {"data": [
        {"timestamp": "bad date", "kwh": "1"},
        {"timestamp": "24/06/2026 00:00", "kwh": "abc", "kvah": "nan"},
        {"timestamp": "24/06/2026 00:30", "kwh": "100"},
        {"timestamp": "24/06/2026 00:30", "kwh": "101"},   # duplicate -> last wins
        {"timestamp": "24/06/2026 01:00", "kwh": "5"},     # register went down
    ]}
    result = services.normalize_energy(payload)
    assert result["warnings"] == {"skippedInvalidTimestamp": 1, "duplicateTimestamps": 1, "registerDecreases": 1}
    assert result["data"][0]["kwh"] is None and result["data"][0]["kvah"] is None
    assert result["data"][-1]["intervalKwh"] is None  # negative difference is not reported as usage


def test_geo_ok_and_strings_parsed():
    c, _ = api(FakeResponse(body={"data": {"latitude": "26.85013563328432", "longitude": "75.77765593203735"}}))
    assert c.get("/api/v1/meters/J100020/geo").json()["data"]["latitude"] == pytest.approx(26.85013563)


@pytest.mark.parametrize("body", [{"data": None}, {"data": {}}, {"data": {"latitude": "x", "longitude": "1"}},
                                  {"data": {"latitude": "95", "longitude": "10"}}])
def test_geo_missing_or_invalid(body):
    c, _ = api(FakeResponse(body=body))
    r = c.get("/api/v1/meters/J100020/geo")
    assert r.status_code == 404 and r.json()["error"] == "geo_unavailable"


def test_transformer_pagination_metadata():
    c, _ = api(FakeResponse(body=DTS))
    body = c.get("/api/v1/distribution-transformers?page=1").json()
    assert (body["total"], body["page"], body["pageSize"]) == (40, 1, 20)
    assert body["data"][1]["capacityKva"] == 63


def test_upstream_429_is_error_not_empty_and_stops_after_3_tries():
    c, session = api(FakeResponse(429, headers={"Retry-After": "7"}))
    r = c.get("/api/v1/meters/J100020/energy")
    assert r.status_code == 429 and r.headers["Retry-After"] == "7"
    assert "rate-limiting" in r.json()["message"]
    assert session.calls == 3  # bounded retries, never infinite


def test_429_then_success_retries():
    c, session = api(FakeResponse(429), FakeResponse(body=ENERGY))
    assert c.get("/api/v1/meters/J100020/energy").status_code == 200 and session.calls == 2


def test_upstream_timeout():
    c, session = api(requests.Timeout())
    assert c.get("/api/v1/meters/J100020/energy").status_code == 504 and session.calls == 3


def test_upstream_500_and_no_internals_leaked():
    c, _ = api(FakeResponse(500))
    r = c.get("/api/v1/meters")
    assert r.status_code == 502 and "Traceback" not in r.text


def test_unexpected_payload_is_502():
    c, _ = api(FakeResponse(body={"oops": 1}))
    assert c.get("/api/v1/meters").status_code == 502


def test_cache_avoids_repeat_requests():
    c, session = api(FakeResponse(body=ENERGY))
    c.get("/api/v1/meters/J100020/energy")
    assert session.calls == 1
    client = PortalClient(session=session, base_url="http://fake")
    client.get_energy("J100020"); client.get_energy("J100020")
    assert session.calls == 2  # second call on same client served from cache


@pytest.mark.parametrize("url", ["/api/v1/meters?page=0", "/api/v1/meters?page=abc",
                                 "/api/v1/meters/bad%20id/energy", "/api/v1/distribution-transformers?page=-1"])
def test_invalid_query_parameters(url):
    c, session = api(FakeResponse(body={"data": []}))
    assert c.get(url).status_code == 422 and session.calls == 0  # rejected before any upstream call
