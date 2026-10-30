# PROTOCOL.md — how the Urja Meter Ops portal works

Investigated on 29 and 30 Oct 2026 with browser DevTools (Network tab) plus live requests from the service in this repo.
The meter page-data findings (§3a) come from the 30 Oct 2026 investigation session; they were recorded in that session's log and are written up here, not re-run while editing this document.
The portal has no public API. Everything below is how its own front end talks to its own back end, so it can change without notice.

**Labels:** `[VERIFIED]` seen live (DevTools or our client against the real portal) · `[INFERRED]` reasoned from what was seen · `[NOT TESTED]` not checked.
Only read-only GET requests were made, plus the normal login.

## 1. What it is
`[VERIFIED]` A SvelteKit web app at `https://urja-ops.flockenergy.tech` for looking up smart meters and distribution transformers. Sidebar: Meters, Transformers, Ops Desk, Sign out. The pages call JSON endpoints under `/portal/`.

## 2. Authentication
`[VERIFIED]` Login is a SvelteKit **form action**:

| | |
|---|---|
| Request | `POST /login`, `Content-Type: application/x-www-form-urlencoded` |
| Body | `email=<username>&password=<password>`. No hidden CSRF field (body length matched exactly) |
| Headers the browser sends | `Accept: application/json`, `Origin: https://urja-ops.flockenergy.tech`, `Referer: …/login`, **`X-Sveltekit-Action: true`**, a browser `User-Agent` |
| Response | HTTP 200, `application/json`, small body (53 bytes) |
| Success signal | JSON body `{"type": "redirect", …}` |
| Session | `Set-Cookie: __Secure-better-auth.session_token=…; Max-Age=3600; Path=/; HttpOnly; Secure; SameSite=Lax` |

Things worth knowing:
- **HTTP 200 does not mean success.** SvelteKit actions put the outcome in the JSON body (`type`). Our client treats only `success` / `redirect` as logged in. `[INFERRED]` a wrong password returns `failure`; I did not send bad credentials to test it.
- **Session lifetime is 1 hour** (`Max-Age=3600`). `[VERIFIED]` A request with **no cookie**, or with an **invalid session cookie**, gets **HTTP 401 with a JSON body** (checked on `/portal/dts` and `/portal/meters/search`). `[VERIFIED]` **Real expiry:** a logged-in session worked (200), and the same session called again ~61 minutes later returned **401 JSON**, identical to the no-cookie case. The client logs in again once on 401/403, which covers this.
- The cookie name shows the portal uses the Better Auth library.
- **The first-login 403 was a missing `Origin` header.** `[VERIFIED]` With no extra headers the portal answers 403 with the text *"Cross-site POST form submissions are forbidden"*, which is SvelteKit's built-in CSRF check. With `Origin` + `Referer` alone the login succeeds (`type: redirect`, cookie set). `X-Sveltekit-Action` and a browser `User-Agent` were **not** needed. I did not test `Origin` and `Referer` separately; SvelteKit's check is on `Origin`. The client still sends all four to match the browser.
- The data endpoints worked in the browser with `Accept: */*`; our client sends `Accept: application/json` and also works.
- `[VERIFIED]` The data endpoints do require the session (401 without it, see above).

## 3. Data endpoints
All `GET`, JSON, session cookie required in the browser.

| Resource | URL | Response shape |
|---|---|---|
| Meter search / list | `/portal/meters/search?q=<text>&page=<n>` | `{data:[…], total, page, pageSize}` — `[VERIFIED]` 403 meters, 20 per page, 21 pages |
| Energy readings | `/portal/meters/<id>/energy` | `{data:[{timestamp, kwh, kvah, voltR}, …]}` |
| Location | `/portal/meters/<id>/geo` | `{data:{latitude, longitude}}` |
| Transformers | `/portal/dts?page=<n>` | `{data:[{code,name,feederCode,capacityKva}], total, page, pageSize}` — `[VERIFIED]` 40 total, 20 per page, 2 pages |
| SvelteKit page data (one meter) | `/meters/<id>/__data.json?x-sveltekit-invalidated=001` | `[VERIFIED]` hierarchy + installation type, SvelteKit "devalue" format (see §3a) |

### Search
- `q` matching is case-insensitive: `j100234` finds `J100234`. `[VERIFIED]`
- There is no separate "meter detail" endpoint. Our `GET /api/v1/meters/{id}` searches for the id and requires an exact `meterId` match.
- Meter ids run `J100000` upward. Each meter has a transformer code (`dtCode`).

### Energy
- `[VERIFIED]` ~30-minute cadence. J100020 returned 337 readings, 2026-06-23 23:30 → 2026-06-30 23:30, with no duplicates, bad timestamps or register decreases.
- **The amount of history differs per meter.** J100234 returned only 8 readings.
- **The data is a fixed snapshot.** It ends on 30 Jun 2026, months before the investigation date, so "last 24 h" means "24 h before the last reading".
- `[VERIFIED]` Timestamps carry **no time zone**. We assume nothing and show them as received.
- `kwh` / `kvah` are cumulative registers (they only increase), not per-interval consumption. `[INFERRED]` the difference between neighbouring `kwh` values is the energy for that interval; the API exposes it as `intervalKwh` (null for the first row or if the register ever decreases).
- `voltR` is presumably R-phase voltage; the units are not stated. A reading can lack `voltR`; we keep it `null`, never `0`.
- Numbers can arrive as strings and rows can arrive out of order; the service parses and sorts.

### Location
Coordinates arrive as numbers or strings; the service converts them and rejects missing or out-of-range values (404 `geo_unavailable`). Seen: J100020 → 26.8501, 75.7777; J100234 → 26.8686, 75.6990 (Jaipur area).

### Transformers
- `capacityKva` is *rated* capacity; there is no load or utilisation data.
- **Page 2 works** (`DT-021` … `DT-040`). My first attempt returned page 1 twice — see §5.
- `[VERIFIED]` All 403 meters point at a transformer that exists: no orphans, no null `dtCode`. The mapping follows the meter order and repeats every 40 meters (J100000 → DT-001, J100020 → DT-021, J100234 → DT-035).

## 3a. Meter page data: installation type and network hierarchy
The search results lack these fields, but the portal's own meter page gets them from an **internal SvelteKit route**:

```
GET /meters/<id>/__data.json?x-sveltekit-invalidated=001      (session cookie required, Cache-Control: private, no-store)
```
Our API exposes it as `GET /api/v1/meters/{id}/hierarchy`.

**Format** `[VERIFIED]` A JSON object `{"type":"data","nodes":[…]}`. Node 0 and 1 are `skip` (layout), node 2 holds the meter. The meter node's `data` is SvelteKit's compact *devalue* format: one flat list in which objects and arrays refer to other entries **by index** instead of nesting. `app/devalue.py` turns it back into normal dicts and lists. It only handles what this portal uses; unknown tagged values are kept as `{"_tag", "_value"}`, never guessed, and a bad reference raises a clean error.

**Decoded content** `[VERIFIED]`
- `meterId`
- `detail`: six nameplate rows, each `{parameterName, parameterValue}`
- `hierarchy`: named levels, top of the network down: **Zone, Circle, Division, Subdivision, Sub Station, Feeder, DT**. A value holds name and code in one string, e.g. `Jaipur Zone 3 (Z-03)`; the service splits it into `name` and `code`.
- Installation types seen in the sample: `CT Operated`, `Whole Current` (not an exhaustive list).
- `[NOT TESTED live]` which of `hierarchy` / `detail` holds "Installation Type" and "Installation Status" was not re-checked in this review. The service reads them from the `hierarchy` object first and falls back to the `detail` rows (covered by a mocked test).

**Validation sample** `[VERIFIED]` 12 meters across `J100000 … J100402`, including Installed, Faulty and Decommissioned: all had all seven levels, and the hierarchy's transformer code equalled the meter's `dtCode` from search every time. **This is 12 of 403 meters.** Complete hierarchy for every meter is not proven. The API therefore omits missing levels and sets `hierarchyComplete: false` rather than inventing them.

**No bulk route** `[VERIFIED]` `/meters/__data.json?x-sveltekit-invalidated=010` returned only the logged-in user's `name` and `email`, no meter list. So hierarchy needs **one extra portal request per meter**, which is why the dashboard fetches it only for the selected meter (and the service caches it for 30 s).

**Unknown meter** `[VERIFIED]` The portal answers **HTTP 200** with an `error` node (status 404 / "not found") in the body. We map that to our **404**; any other error node becomes a **502**.

**Logged-out request** `[INFERRED]` SvelteKit normally answers a page-data request that needs login with `{"type":"redirect", …}` (still HTTP 200). The client treats such a body like a 401: it logs in again once and never caches it. This was **not observed live**; it is covered by a mocked test.

**Why use an internal route at all?** There is no explicit hierarchy endpoint, and this is where the portal's own page gets the data. Trade-off: it is framework-internal, undocumented, and its format can change without notice. It is isolated in `get_meter_page()` (client), `devalue.py` and `normalize_meter_profile()` (service), so a change breaks one route and not the rest of the API.

## 4. Rate limiting
`[VERIFIED]` Early on, requesting `/energy` too often returned **HTTP 429**. Actual limits are `[NOT TESTED]`. Mitigations in the client: energy is fetched only for the selected meter, on a button click; at most 3 attempts; `Retry-After` honoured (capped at 10 s) with exponential backoff otherwise; 30 s in-memory cache; a persistent 429 becomes a 429 from our API, never an empty list.

## 5. Quirks and surprises
1. **HTTP 200 on a login that did not succeed.** The real result is in the JSON `type`. `redirect` means success.
2. **The 403 on login from Python** was only the missing `Origin` header (SvelteKit's CSRF check); see §2.
3. **A `?` inside a path breaks paging.** `PORTAL_DTS_PATH=/portal/dts?page=1` in `.env` made the request `…?page=1&page=2`, and the portal used the first `page`, so every page looked like page 1. Found by comparing our output with the browser's DevTools requests (response sizes 1680 vs 1692 bytes). The client now strips any query string from that setting, and a test covers it.
4. **Static, partial data.** Readings stop at 30 Jun 2026; the number of readings varies by meter.
5. **Detail fields missing from search results.** Installation type and the network hierarchy are in the meter's page data, not the search results (§3a). The search-based `GET /api/v1/meters` therefore still returns `installationType: null` and `hierarchy: null`; the new `/hierarchy` route fills them. We never guess them.
6. **Page data reports errors with HTTP 200.** An unknown meter is a 200 response containing an `error` node, so the status code alone is not enough (§3a).

## 6. Not explored / next
- Hierarchy for all 403 meters: only 12 were sampled. Checking more, carefully and slowly, would be the next step.
- A network explorer (browse Zone → … → Transformer and see its meters) is not built; with no bulk route it would need one request per meter.
- Which object (`hierarchy` or `detail`) carries installation type was not re-checked (§3a).
- The client's automatic re-login after a real expiry was not run end to end against the live portal (unit tests use mocks; the portal's 401 on expiry was verified separately). Rate-limit thresholds were **deliberately not probed**: it would mean hammering a server I was told to treat as read-only. What is known: 429 exists, and the client backs off using `Retry-After`.

## 7. How our API maps to the portal
| Our route | Portal resource |
|---|---|
| `GET /api/v1/meters` | `/portal/meters/search` |
| `GET /api/v1/meters/{id}` | search with `q={id}`, exact match |
| `GET /api/v1/meters/{id}/energy` | `/portal/meters/{id}/energy` |
| `GET /api/v1/meters/{id}/geo` | `/portal/meters/{id}/geo` |
| `GET /api/v1/meters/{id}/hierarchy` | `/meters/{id}/__data.json?x-sveltekit-invalidated=001` (decoded) |
| `GET /api/v1/distribution-transformers` | `/portal/dts` |