"""Mocked tests for the meter-profile (hierarchy) endpoint, the __data.json decoder and the login result check.
No live requests are made."""
import types

import pytest
from fastapi.testclient import TestClient

from app import portal_client
from app.devalue import unflatten
from app.main import app, get_client
from app.portal_client import PortalClient, PortalError

# A tiny __data.json "devalue" page: a flat list where values refer to each other by index.
FLAT = [
    {"meterId": 1, "hierarchy": 2},
    "J100002",
    {"Installation Status": 3, "Installation Type": 4, "Zone": 5, "Circle": 6, "Division": 7,
     "Subdivision": 8, "Sub Station": 9, "Feeder": 10, "DT": 11},
    "Installed", "CT Operated", "Jaipur Zone 3 (Z-03)", "Circle 3 (C-03)", "Division 3 (D-03)",
    "Subdivision 3 (SD-03)", "Substation 3 (SS-03)", "Feeder 3 (F-003)", "Vaishali DT 3 (DT-003)",
]


def page_payload(flat):
    return {"type": "data", "nodes": [{"type": "skip"}, {"type": "skip"}, {"type": "data", "data": flat}]}


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class OneShotSession:
    def __init__(self, response):
        self.response, self.last = response, None

    def request(self, method, url, params=None, headers=None, timeout=None, data=None):
        self.last = (url, params)
        return self.response


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def profile_client(body):
    session = OneShotSession(Resp(body=body))
    app.dependency_overrides[get_client] = lambda: PortalClient(session=session, base_url="http://fake")
    return TestClient(app), session


def test_unflatten_resolves_references():
    assert unflatten(FLAT)["hierarchy"]["Zone"] == "Jaipur Zone 3 (Z-03)"


def test_unflatten_rejects_out_of_range_reference():
    with pytest.raises(ValueError):
        unflatten([{"a": 99}])


def test_hierarchy_endpoint_full():
    c, session = profile_client(page_payload(FLAT))
    r = c.get("/api/v1/meters/J100002/hierarchy")
    body = r.json()
    assert r.status_code == 200
    assert body["installationType"] == "CT Operated" and body["hierarchyComplete"] is True
    assert [l["level"] for l in body["hierarchy"]] == [
        "zone", "circle", "division", "subdivision", "substation", "feeder", "transformer"]
    assert body["hierarchy"][-1] == {"level": "transformer", "name": "Vaishali DT 3", "code": "DT-003"}
    # the query goes in params, not baked into the path (the earlier ?page=1 bug)
    assert session.last == ("http://fake/meters/J100002/__data.json", {"x-sveltekit-invalidated": "001"})


def test_hierarchy_missing_and_empty_levels_are_omitted_not_invented():
    flat = [dict(FLAT[0]), "J100002", {"Installation Status": 3, "Zone": 4, "Feeder": 5}, "Installed", "Zone A (Z-01)", "   "]
    body = profile_client(page_payload(flat))[0].get("/api/v1/meters/J100002/hierarchy").json()
    assert [l["level"] for l in body["hierarchy"]] == ["zone"]
    assert body["hierarchyComplete"] is False and body["installationType"] is None


def test_hierarchy_wrong_meter_or_shape_is_502():
    assert profile_client(page_payload(FLAT))[0].get("/api/v1/meters/J100003/hierarchy").status_code == 502
    assert profile_client({"oops": 1})[0].get("/api/v1/meters/J100002/hierarchy").status_code == 502


def error_payload(**node):
    return {"type": "data", "nodes": [{"type": "skip"}, {"type": "skip"}, {"type": "error", **node}]}


def test_hierarchy_unknown_meter_is_404():
    # the portal answers HTTP 200 with an "error" node for a meter that does not exist
    r = profile_client(error_payload(status=404, error={"message": "Not Found"}))[0].get("/api/v1/meters/J999999/hierarchy")
    assert r.status_code == 404 and r.json()["error"] == "meter_not_found"


def test_hierarchy_other_error_node_stays_502():
    r = profile_client(error_payload(status=500, error={"message": "Internal Error"}))[0].get("/api/v1/meters/J100002/hierarchy")
    assert r.status_code == 502


def test_hierarchy_rejects_bad_meter_id():
    assert profile_client(page_payload(FLAT))[0].get("/api/v1/meters/bad%20id/hierarchy").status_code == 422


# ---- login result check (the portal answers HTTP 200 even when login fails) ----
class LoginSession:
    def __init__(self, result):
        self.result, self.cookies, self.posted = result, [], None

    def get(self, url, **kw):
        return Resp(200)

    def post(self, url, data=None, headers=None, timeout=None):
        self.posted = headers
        self.cookies = [types.SimpleNamespace(name="session")]
        return Resp(200, {"type": self.result}, {"Content-Type": "application/json"})


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("PORTAL_USERNAME", "u")
    monkeypatch.setenv("PORTAL_PASSWORD", "p")
    monkeypatch.setenv("PORTAL_LOGIN_PATH", "/login")


@pytest.mark.parametrize("result", ["redirect", "success"])
def test_login_accepts_success_and_redirect(creds, result):
    session = LoginSession(result)
    client = PortalClient(session=session, base_url="http://fake")
    client.login()
    assert client.logged_in
    assert session.posted["Origin"] == "http://fake"  # the header whose absence caused the 403


@pytest.mark.parametrize("result", ["failure", "error", None])
def test_login_rejects_failure_even_with_http_200(creds, result):
    client = PortalClient(session=LoginSession(result), base_url="http://fake")
    with pytest.raises(PortalError) as err:
        client.login()
    assert err.value.kind == "auth" and not client.logged_in