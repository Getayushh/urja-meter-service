"""Tests for the meter page-data (hierarchy) feature. All MOCKED: no live portal requests."""
import pytest
from fastapi.testclient import TestClient

from app import portal_client
from app.devalue import unflatten
from app.main import app, get_client
from app.portal_client import PortalClient


def encode(obj):
    """Tiny test-only encoder that produces SvelteKit's flat 'devalue' list (objects refer to indexes)."""
    data = []

    def add(value):
        i = len(data)
        data.append(None)
        if isinstance(value, dict):
            data[i] = {k: add(v) for k, v in value.items()}
        elif isinstance(value, list):
            data[i] = [add(v) for v in value]
        else:
            data[i] = value
        return i

    add(obj)
    return data


HIERARCHY = {"Zone": "Jaipur Zone 3 (Z-03)", "Circle": "Circle 3 (C-03)", "Division": "Division 1 (D-01)",
             "Subdivision": "Subdivision 7 (SD-07)", "Sub Station": "Substation 3 (SS-03)",
             "Feeder": "Feeder 21 (F-021)", "DT": "Malviya Nagar DT 21 (DT-021)",
             "Installation Status": "Installed", "Installation Type": "CT Operated"}


def page_payload(meter_id="J100020", hierarchy=None, detail=None):
    page = {"meterId": meter_id, "hierarchy": HIERARCHY if hierarchy is None else hierarchy}
    if detail is not None:
        page["detail"] = detail
    return {"type": "data", "nodes": [{"type": "skip"}, {"type": "skip"}, {"type": "data", "data": encode(page)}]}


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.headers = status, body, {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, body, status=200):
        self.body, self.status, self.calls = body, status, []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        return FakeResponse(self.status, self.body)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    app.dependency_overrides.clear()


def api(body, status=200):
    session = FakeSession(body, status)
    app.dependency_overrides[get_client] = lambda: PortalClient(session=session, base_url="http://fake")
    return TestClient(app), session


# ---------- devalue ----------
def test_devalue_roundtrip_and_shared_reference():
    shared = ["a"]
    assert unflatten(encode({"x": shared, "y": shared})) == {"x": ["a"], "y": ["a"]}


def test_devalue_specials_and_cycle():
    assert unflatten([{"a": -1}]) == {"a": None}                 # undefined -> None
    cyc = [{"me": 0}]                                            # object that refers to itself
    out = unflatten(cyc)
    assert out["me"] is out


@pytest.mark.parametrize("bad", [[], [{"a": 99}], [{"a": "x"}], [["Date"]][:0]])
def test_devalue_bad_references_raise_value_error(bad):
    with pytest.raises(ValueError):
        unflatten(bad)


def test_devalue_too_deep_is_value_error():
    deep = [[i + 1] for i in range(5000)] + ["end"]
    with pytest.raises(ValueError):
        unflatten(deep)


# ---------- /hierarchy route ----------
def test_hierarchy_happy_path_and_request_shape():
    c, session = api(page_payload())
    body = c.get("/api/v1/meters/J100020/hierarchy").json()
    assert [l["level"] for l in body["hierarchy"]] == ["zone", "circle", "division", "subdivision", "substation", "feeder", "transformer"]
    assert body["hierarchy"][0] == {"level": "zone", "name": "Jaipur Zone 3", "code": "Z-03"}
    assert body["hierarchy"][-1]["code"] == "DT-021"
    assert body["hierarchyComplete"] is True and body["installationType"] == "CT Operated"
    url, params = session.calls[0]
    assert url.endswith("/meters/J100020/__data.json") and params == {"x-sveltekit-invalidated": "001"}


def test_missing_levels_are_omitted_not_invented():
    partial = {k: v for k, v in HIERARCHY.items() if k not in ("Circle", "Feeder")}
    body = api(page_payload(hierarchy=partial))[0].get("/api/v1/meters/J100020/hierarchy").json()
    assert len(body["hierarchy"]) == 5 and body["hierarchyComplete"] is False
    assert "circle" not in [l["level"] for l in body["hierarchy"]]


def test_installation_type_falls_back_to_detail_rows():
    only_levels = {k: v for k, v in HIERARCHY.items() if not k.startswith("Installation")}
    detail = [{"parameterName": "Installation Type", "parameterValue": "Whole Current"}]
    body = api(page_payload(hierarchy=only_levels, detail=detail))[0].get("/api/v1/meters/J100020/hierarchy").json()
    assert body["installationType"] == "Whole Current" and body["installationStatus"] is None


def test_label_without_code_keeps_name_and_null_code():
    body = api(page_payload(hierarchy={"Zone": "Jaipur Zone 3"}))[0].get("/api/v1/meters/J100020/hierarchy").json()
    assert body["hierarchy"] == [{"level": "zone", "name": "Jaipur Zone 3", "code": None}]


def test_lowercase_id_matches_portal_canonical_id():
    r = api(page_payload("J100020"))[0].get("/api/v1/meters/j100020/hierarchy")
    assert r.status_code == 200 and r.json()["meterId"] == "J100020"


def test_unknown_meter_error_node_is_404():
    err = {"type": "data", "nodes": [{"type": "skip"}, {"type": "skip"}, {"type": "error", "status": 404, "error": {"message": "Not Found"}}]}
    r = api(err)[0].get("/api/v1/meters/J999999/hierarchy")
    assert r.status_code == 404 and r.json()["error"] == "meter_not_found"


def test_other_error_node_is_502():
    err = {"type": "data", "nodes": [{"type": "error", "status": 500, "error": {"message": "Internal Error"}}]}
    assert api(err)[0].get("/api/v1/meters/J100020/hierarchy").status_code == 502


@pytest.mark.parametrize("body", [{"nodes": "x"}, {"oops": 1}, page_payload("J100021")])
def test_malformed_or_wrong_meter_payload_is_502(body):
    assert api(body)[0].get("/api/v1/meters/J100020/hierarchy").status_code == 502


def test_login_redirect_node_is_auth_error_and_not_cached():
    c, session = api({"type": "redirect", "status": 302, "location": "/login"})
    r = c.get("/api/v1/meters/J100020/hierarchy")
    assert r.status_code == 502 and "log in" in r.json()["message"]
    client = PortalClient(session=session, base_url="http://fake")
    with pytest.raises(portal_client.PortalError):
        client.get_meter_page("J100020")
    assert client._cache == {}
