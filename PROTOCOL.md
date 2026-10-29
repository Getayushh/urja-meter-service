# PROTOCOL.md — Urja Meter Ops investigation notes

**Labels used:** `[OBSERVED]` seen in screenshots/DevTools during earlier investigation · `[INFERRED]` reasoned from observations ·
`[VERIFIED-MOCK]` implemented and tested against *mocked* responses only · `[NOT VERIFIED]` unknown.

> No live portal request was made while building this project (the build environment could not reach the portal, and no credentials were used).
> Nothing below is marked "verified live". The JSON endpoints are **observed portal resources**, not an officially documented public API.

## 1. What Urja Meter Ops is
`[OBSERVED]` A browser portal (https://urja-ops.flockenergy.tech) for looking up electricity meters and distribution-network assets. Navigation: Meters, Transformers, Ops Desk, Sign out.

## 2. Pages
| Page | Observed |
|---|---|
| Meter list | Search box and pagination |
| Meter detail (J100020) | Prominent meter id, **Nameplate** section, **Location** section, hierarchy breadcrumb |

## 3. Nameplate and Location (meter J100020) `[OBSERVED]`
| Field | Value |
|---|---|
| meterId | J100020 |
| serialNo | AL20290 |
| make | Allied |
| phaseType | three |
| installationStatus | Installed |
| installationType | CT Operated |

Location: latitude 26.85013563328432, longitude 75.77765593203735. These are values from the UI, **not** confirmed JSON field names.

## 4. Hierarchy for J100020 `[OBSERVED]`
Jaipur Zone 3 (Z-03) → Circle 3 (C-03) → Division 1 (D-01) → Subdivision 7 (SD-07) → Substation 3 (SS-03) → Feeder 21 (F-021) → Malviya Nagar DT 21 (DT-021).
`[NOT VERIFIED]` where/how the API returns this. The new API therefore returns `hierarchy: null` unless the portal supplies a `hierarchy` field, and the dashboard says "not available". It is never reconstructed from ids or names.

## 5. Endpoints
| Request | Status | What we know |
|---|---|---|
| `GET /portal/meters/search?q=&page=2` (and `page=1`) | 200, `application/json` `[OBSERVED]` | Params `q`, `page`. Response body **not seen**; schema `[NOT VERIFIED]`. |
| `GET /portal/meters/J100020/energy` | 200, JSON, ~27,564 bytes `[OBSERVED]` | `{"data":[{timestamp,kwh,kvah,voltR}]}` |
| `GET /portal/meters/J100020/geo` | 200 `[OBSERVED]` | `{"data":{"latitude":"…","longitude":"…"}}` (strings) |
| `dts?page=1` | body seen, full URL prefix **not seen** | `{"data":[{code,name,feederCode,capacityKva}],"total":40,"page":1,"pageSize":20}` |
| `GET /meters/J100020/__data.json?x-sveltekit-invalidated=001` | 200, JSON, ~699 bytes, `Cache-Control: private, no-store` `[OBSERVED]` | Body not seen |

### Search
An earlier notes example (`data`, `total: 10`, `page`, `pageSize: 20`, field `installStatus`, `dtCode`) is **illustrative, not a captured response**. Our code tolerates both `installStatus` and `installationStatus`, and returns `total`/`pageSize` as `null` when absent.

### Energy
`[OBSERVED]` timestamp format `DD/MM/YYYY HH:mm`; numbers are strings; sample readings 30 minutes apart; values increase; the last sample had no `voltR`.
`[INFERRED]` `kwh`/`kvah` are cumulative registers; consecutive differences estimate interval energy. `voltR` is probably R-phase voltage (units unknown).
`[NOT VERIFIED]` full record count, time range, timezone, guaranteed cadence, register reset/rollover behaviour.
Our API parses the format explicitly, sorts, keeps missing values as `null`, reports duplicates/invalid rows in `warnings`, and sets `intervalKwh` to `null` when the difference would be negative.

### Transformers
`[OBSERVED]` 20 records on page 1 (DT-001 … DT-020, capacities 63–400 kVA), 40 total, pageSize 20. Page 2 contents **not seen** — not assumed. `capacityKva` is *rated capacity* only; no load/utilization data was observed.
`[NOT VERIFIED]` the URL prefix. We use `/portal/dts` as a **placeholder default**, overridable with `PORTAL_DTS_PATH` in `.env`.

### SvelteKit `__data.json`
`[OBSERVED]` A page-data request that the SvelteKit front end makes for the meter page. `[INFERRED]` it carries data for rendering that page. We don't use it because its body is unknown, it is an internal framework route (format can change between builds), and the explicit `search`/`energy`/`geo` resources have clearer boundaries.

## 6. Pagination evidence
Meters: `page` param observed. Transformers: `total`, `page`, `pageSize` observed on page 1 (40 / 1 / 20 → 2 pages `[INFERRED]`).

## 7. Authentication
`[OBSERVED]` The portal was used logged in and a session cookie was present.
`[NOT VERIFIED]` login URL, form field names, CSRF, cookie names/lifecycle, which endpoints need auth, whether a fresh `requests.Session` can reproduce the browser session.
Implementation: `portal_client.login()` runs only if `PORTAL_LOGIN_PATH` is set; field names come from env vars (defaults `username`/`password` are **guesses**). Credentials come from env only. Copying a browser cookie is deliberately not supported.

## 8. HTTP 429 `[OBSERVED]`
Our later backend attempts got repeated `429 Too Many Requests` on `/portal/meters/{id}/energy`. `[INFERRED]` we requested energy too often / for too many meters. Actual limits `[NOT VERIFIED]`.
Mitigations `[VERIFIED-MOCK]`: energy is fetched only for the one selected meter, only when the user clicks; max 3 attempts; honours `Retry-After` (capped at 10 s); exponential backoff (1 s, 2 s) otherwise; 30 s in-memory cache; a persistent 429 becomes an HTTP 429 from our API with a clear message — never an empty list.

## 9. How the new API uses the data
| Our route | Upstream resource |
|---|---|
| `/api/v1/meters` | `/portal/meters/search` |
| `/api/v1/meters/{id}` | same search with `q={id}`, exact `meterId` match (no separate details endpoint observed) |
| `/api/v1/meters/{id}/energy` | `/portal/meters/{id}/energy` |
| `/api/v1/meters/{id}/geo` | `/portal/meters/{id}/geo` |
| `/api/v1/distribution-transformers` | `dts` (prefix unverified) |

## 10. Still unverified
Search response schema · whether search by id returns the detail fields · login flow · transformer URL prefix · hierarchy in API · timezone · energy history size & semantics · rate-limit policy · page 2 of transformers · whether all endpoints require auth.
