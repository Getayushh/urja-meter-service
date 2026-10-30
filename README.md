# Urja Meter Service

A read-only **FastAPI** service, plus a small web dashboard, over the *Urja Meter Ops* portal (https://urja-ops.flockenergy.tech). Built for the Flock Energy take-home.

The portal only works in a browser. This service logs in on your behalf, calls the portal's own JSON endpoints, and returns clean, consistent JSON (numbers as numbers, ISO timestamps, `null` for missing values, clear errors) so other programs never touch the portal.

| Read this | For |
|---|---|
| [PROTOCOL.md](PROTOCOL.md) | How the portal actually works: login, endpoints, quirks |
| [openapi.json](openapi.json) | OpenAPI spec of *this* API (also live at `/docs`) |
| [REFLECTION.md](REFLECTION.md) | Reflection questions |

**Status:** verified against the live portal (login, meters, energy, location, transformers, and meter hierarchy for a 12-meter sample). Unit tests use mocked responses only.

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

Hierarchy example (`GET /api/v1/meters/J100020/hierarchy`; shape from the mocked test fixture):
```json
{"meterId":"J100020","installationStatus":"Installed","installationType":"CT Operated","hierarchyComplete":true,
 "hierarchy":[{"level":"zone","name":"Jaipur Zone 3","code":"Z-03"}, "...", {"level":"transformer","name":"Malviya Nagar DT 21","code":"DT-021"}]}
```

## Endpoints
| Route | Notes |
|---|---|
| `GET /api/v1/meters?q=&page=1` | Search/list, 20 per page, case-insensitive |
| `GET /api/v1/meters/{meter_id}` | Exact id match; 404 if none |
| `GET /api/v1/meters/{meter_id}/energy` | Sorted readings, derived `intervalKwh`, data-quality `warnings` |
| `GET /api/v1/meters/{meter_id}/geo` | Coordinates; 404 `geo_unavailable` if missing or invalid |
| `GET /api/v1/meters/{meter_id}/hierarchy` | Installation type + Zone → … → Transformer, read from the portal's meter page data. One extra portal request per call. Missing levels are omitted and `hierarchyComplete` is `false`; unknown meter is 404 |
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
app/services.py       parsing and normalisation (never invents data), incl. the meter profile / hierarchy
app/devalue.py        decodes SvelteKit's flat `__data.json` format into normal dicts and lists
app/main.py           routes, validation, error mapping, serves the dashboard
frontend/             index.html, style.css, app.js (plain JS, SVG chart, no libraries)
tests/                mocked tests (no live requests): API, error handling, hierarchy and the devalue decoder
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
- Hierarchy comes from the portal's internal `__data.json` page data, because the search results lack it. Only 100 of 403 meters were checked; the API reports `hierarchyComplete` instead of assuming (see PROTOCOL.md §3a).

## Design decisions and trade-offs
- **Normalise at the edge.** Parsing lives in `services.py`; missing values stay `null` rather than `0` or a guess.
- **Be gentle with the portal.** Energy is loaded only for the selected meter on demand. Up to 3 attempts, `Retry-After` honoured, 30 s in-memory cache, and a persistent 429 is shown as an error, never an empty list.
- **Fail clearly.** Bad input gets a 422 before any portal call; unexpected upstream shapes get a 502.
- **Login treats the JSON body as the truth,** because the portal answers HTTP 200 even for a failed login (see PROTOCOL.md).
- **One origin.** FastAPI serves the dashboard, so there's nothing to configure.
- **Dashboard safety.** API data is only written with `textContent`, never `innerHTML`.
- **Search first, details second.** The dashboard shows the search results directly under the search box. Picking a meter fetches its hierarchy and location (2 light requests); the energy readings load only when you press the button. Typing searches after a 700 ms pause, and stale answers are ignored.
- **Hierarchy in one place.** Reading `__data.json` is isolated in `get_meter_page()`, `devalue.py` and `normalize_meter_profile()`. If the portal changes that internal format, only `/hierarchy` breaks.

## What I left out, and why
- Database or local index: 403 meters don't justify one. At around 100k+ meters, filtering across attributes would need an index (SQLite or Postgres, refreshed in the background).
- A bulk hierarchy or network explorer: the portal has no bulk hierarchy route (PROTOCOL.md §3a), so it would cost one request per meter.
- Merging hierarchy into the meter list: the search endpoint stays one request per page; hierarchy is fetched per selected meter. The list's `hierarchy` field is always `null`.
- Auth on our own API: it's meant to run locally; anyone who can reach it can use the portal credentials in `.env`. Don't expose it publicly as is.
- Docker, Redis, a charting library, alarms/anomaly detection.

## Known limitations
- The 30 s cache is per process and has no invalidation beyond the TTL; the portal data is static, so staleness hasn't mattered.
- Sessions last 1 h. A missing, invalid or really expired session returns 401 JSON (all tested live), and the client re-logs in once on 401/403. That re-login was verified with mocks only, not by letting a live session expire inside the service.
- Two requests that expire at once could each log in. A lock would fix that.
- `__data.json` is an internal SvelteKit route with no stability promise; the decoder only handles what this portal is known to use. Hierarchy was checked on 100 of 403 meters.
- Which portal object holds installation type was not re-checked live; the service tries `hierarchy` first, then the `detail` rows.
- A "log in again" redirect node from the page-data route is handled (and mock-tested) but was never seen live.
- Rate-limit thresholds are unknown by choice (the brief says read-only; I did not hammer the portal). The 429 handling is based on the 429s seen early on.

## With more time
Check hierarchy for all 403 meters (slowly, with the 429 behaviour in mind) and build a network explorer. Add a date filter and daily aggregation for energy. Add a small live contract test that I run by hand. Add a login lock and test session expiry. Pull transformer details into the meter response.

## Publishing safely
`.env`, `.venv/` and caches are git-ignored. Before pushing, run `git status` and confirm `.env` isn't listed. If a secret was ever committed, rotate it; deleting the file isn't enough.