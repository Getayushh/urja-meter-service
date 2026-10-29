# Urja Meter Service

A read-only **FastAPI** service, plus a small web dashboard, over the *Urja Meter Ops* portal (https://urja-ops.flockenergy.tech). Built for the Flock Energy take-home.

The portal only works in a browser. This service logs in on your behalf, calls the portal's own JSON endpoints, and returns clean, consistent JSON (numbers as numbers, ISO timestamps, `null` for missing values, clear errors) so other programs never touch the portal.

| Read this | For |
|---|---|
| [PROTOCOL.md](PROTOCOL.md) | How the portal actually works: login, endpoints, quirks |
| [openapi.json](openapi.json) | OpenAPI spec of *this* API (also live at `/docs`) |
| [REFLECTION.md](REFLECTION.md) | Reflection questions |

**Status:** verified against the live portal (login, meters, energy, location, transformers). Unit tests use mocked responses only.

## Quick start
Needs Python 3.10+.
```bash
git clone <this-repo> && cd urja-meter-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put the portal username and password in .env
uvicorn app.main:app --port 8000
```
Then open:
- **Dashboard:** http://127.0.0.1:8000/
- **API docs (Swagger):** http://127.0.0.1:8000/docs
- **Health:** http://127.0.0.1:8000/health (this checks our process only, not the portal)

One process serves both the API and the dashboard, so there's no second server and no CORS setup.

### Configuration (`.env`)
| Variable | Meaning |
|---|---|
| `PORTAL_BASE_URL` | Portal address |
| `PORTAL_USERNAME`, `PORTAL_PASSWORD` | Credentials (never committed; `.env` is git-ignored) |
| `PORTAL_LOGIN_PATH`, `PORTAL_LOGIN_USER_FIELD`, `PORTAL_LOGIN_PASS_FIELD` | `/login`, `email`, `password` |
| `PORTAL_DTS_PATH` | `/portal/dts` (path only, no `?page=`) |

## Sample request
```bash
curl "http://127.0.0.1:8000/api/v1/meters?q=J1000&page=1"
```
```json
{"data":[{"meterId":"J100000","serialNo":"SE33962","make":"HPL","phaseType":"single",
          "installationStatus":"Decommissioned","installationType":null,"dtCode":"DT-001","hierarchy":null}],
 "total":403,"page":1,"pageSize":20}
```

## Endpoints
| Route | Notes |
|---|---|
| `GET /api/v1/meters?q=&page=1` | Search/list, 20 per page, case-insensitive |
| `GET /api/v1/meters/{meter_id}` | Exact id match; 404 if none |
| `GET /api/v1/meters/{meter_id}/energy` | Sorted readings, derived `intervalKwh`, data-quality `warnings` |
| `GET /api/v1/meters/{meter_id}/geo` | Coordinates; 404 `geo_unavailable` if missing or invalid |
| `GET /api/v1/distribution-transformers?page=1` | 40 transformers over 2 pages, rated capacity only |
| `GET /health` | Local liveness |

Errors are `{"error": "...", "message": "..."}` with 404, 422 (bad parameters, rejected before any portal call), 429 (portal rate limit, with `Retry-After`), 502 (bad upstream data) or 504 (timeout). Internals are never leaked.

## How it's built
```
Browser dashboard (frontend/) -> FastAPI (app/main.py) -> services.py (normalise)
                                                       -> portal_client.py -> Urja portal
```
```
app/portal_client.py  login, GET, timeouts, bounded retries, Retry-After, 30 s cache, safe errors
app/services.py       parsing and normalisation (never invents data)
app/main.py           routes, validation, error mapping, serves the dashboard
frontend/             index.html, style.css, app.js (plain JS, SVG chart, no libraries)
tests/test_api.py     mocked tests (no live requests)
```
Only `portal_client.py` talks to the portal. It is one process with no database or build step, which suits a read-only wrapper.

### Run the tests
```bash
pytest -q
```

### Regenerate the OpenAPI spec
```bash
python -c "import json; from app.main import app; json.dump(app.openapi(), open('openapi.json','w'), indent=2)"
```

## Assumptions
- `kwh` / `kvah` are cumulative registers, so `intervalKwh` is the difference between neighbouring readings (null for the first reading or after a decrease).
- Timestamps have no time zone in the portal data. I don't convert them; they're returned as received.
- `voltR` is probably R-phase voltage, but the units are unknown. It's labelled as such.
- There's no detail endpoint, so a meter is fetched by searching its id and requiring an exact match.
- The portal's data is a fixed snapshot (readings end 30 Jun 2026), so dashboard ranges ("24h") are measured back from the last reading, not from today.

## Design decisions and trade-offs
- **Normalise at the edge.** Parsing lives in `services.py`; missing values stay `null` rather than `0` or a guess.
- **Be gentle with the portal.** Energy is loaded only for the selected meter on demand. Up to 3 attempts, `Retry-After` honoured, 30 s in-memory cache, and a persistent 429 is shown as an error, never an empty list.
- **Fail clearly.** Bad input gets a 422 before any portal call; unexpected upstream shapes get a 502.
- **Login treats the JSON body as the truth,** because the portal answers HTTP 200 even for a failed login (see PROTOCOL.md).
- **One origin.** FastAPI serves the dashboard, so there's nothing to configure.
- **Dashboard safety.** API data is only written with `textContent`, never `innerHTML`.

## What I left out, and why
- Database or local index: 403 meters don't justify one. At around 100k+ meters, filtering across attributes would need an index (SQLite or Postgres, refreshed in the background).
- Hierarchy and installation type: the search results don't include them, so the API returns `null` (see PROTOCOL.md §5). The portal's own page shows them, probably via `__data.json`, which I haven't inspected.
- Auth on our own API: it's meant to run locally; anyone who can reach it can use the portal credentials in `.env`. Don't expose it publicly as is.
- Docker, Redis, a charting library, alarms/anomaly detection.

## Known limitations
- The 30 s cache is per process and has no invalidation beyond the TTL; the portal data is static, so staleness hasn't mattered.
- Session expiry (1 h) is handled by one re-login on 401/403, but I haven't seen what the portal returns on expiry.
- Two requests that expire at once could each log in. A lock would fix that.
- No browser-level tests for the dashboard.
- The first-login 403 was fixed by adding browser-like headers; I didn't isolate which one mattered.

## With more time
Inspect `__data.json` for hierarchy and installation type, then build the network explorer. Add a date filter and daily aggregation for energy. Add a small live contract test that I run by hand. Add a login lock and test session expiry. Pull transformer details into the meter response.

## Publishing safely
`.env`, `.venv/` and caches are git-ignored. Before pushing, run `git status` and confirm `.env` isn't listed. If a secret was ever committed, rotate it; deleting the file isn't enough.