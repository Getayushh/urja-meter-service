# Reflection


**What assumptions did you make?**
That `kwh`/`kvah` are cumulative registers (they only ever rise) so the difference between readings is interval energy; that timestamps have no usable time zone, so I return them as received; that `voltR` is R-phase voltage with unknown units; and that searching a meter id with an exact match is the right "detail" lookup, because I found no detail endpoint. I also chose to show `hierarchy` and `installationType` as `null` rather than reconstruct them, since the search results don't contain them.

**Which part was the most difficult, and how did you get unstuck?**
Getting a Python client logged in. It was rejected with 403 although the browser worked. Reading the login request in DevTools showed the portal is a SvelteKit app: the browser sends `Origin`, `Referer`, `X-Sveltekit-Action: true` and a browser `User-Agent`, and the form has just `email` and `password`. After copying those headers, the login returned HTTP 200 but my "success" check still failed, because the real outcome is in the JSON body (`type: "redirect"`). Later a small probe script (`scripts/probe_portal.py`) showed the 403 body was SvelteKit's CSRF message, and that a missing `Origin` header was the whole cause. Reading what the browser actually did, instead of guessing, solved it.

**If you had another day, what would you improve?**
Inspect the `__data.json` route, which probably carries the installation type and the Zone → Circle → … → Transformer hierarchy, and build the network explorer. Add a date filter and aggregation for energy. Add a login lock and test what the portal returns when the 1-hour session expires. Add a small manual live contract test.

**What mistake did you make while solving this?**
Several. (1) I first built against mocked responses and wrote "not verified live" everywhere, so real problems only appeared later. (2) I wrote `PORTAL_DTS_PATH=/portal/dts?page=1` in `.env`. The client then sent `page` twice, the portal used the first, and page 2 of the transformers came back identical to page 1. My orphan check then wrongly reported 200 meters with an unknown transformer. Comparing response sizes in DevTools (1680 vs 1692 bytes) showed the portal really did return different data, which pointed the blame back at my config. The client now strips query strings from that setting, with a regression test. (3) My first login check was too strict (`success` only), and I also mixed up two frontend files so `index.html` contained JavaScript.

**If you were reviewing your own submission, what would you criticise?**
The tests only use mocks, so the live behaviours (login, the real field names, the `?page` bug) were caught by hand, not by a test. The service has no authentication of its own and holds the portal credentials. The cache and re-login are per-process and unlocked. Installation type and hierarchy are missing from the API even though the portal shows them. I verified the real one-hour expiry (401 JSON) with the probe script, but the service's automatic re-login has only been tested with mocks, and I chose not to probe rate-limit thresholds. The dashboard has no automated tests.

