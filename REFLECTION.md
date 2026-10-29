# REFLECTION.md

1. **What assumptions did you make?**
   - Search by meter id (`q=<id>`) returns that meter with its detail fields (no details endpoint was observed).
   - `kwh`/`kvah` are cumulative registers; timestamps are local time of unknown timezone.
   - The transformer route is `/portal/dts` (placeholder, configurable) and the login flow is configured via env (not captured).
   - Hierarchy is not exposed in a form I could verify, so it's shown as unavailable.

2. **Which part was most difficult, and how did you get unstuck?**
   The hardest part was working with incomplete evidence: several response bodies and the login flow were never captured. I handled that by separating observed / inferred / unverified facts, isolating all portal access in `portal_client.py`, and testing with clearly labelled mock fixtures.
   *[Add your own experience here: what you personally found hard and what helped.]*

3. **If you had another day, what would you improve?**
   Verify the real login and search schemas against the live portal, confirm the transformer URL, find the hierarchy data source, measure the real rate limit, add a clean per-meter energy date filter/aggregation, and add a small contract test run manually against the live portal.

4. **What mistake did you make?**
   My earlier backend attempts requested `/energy` too aggressively and hit repeated HTTP 429s. The fix was architectural: fetch energy only for the one selected meter on demand, with bounded retries and a short cache.
   *[Add any other mistake you personally made.]*

5. **If you reviewed your own submission, what would you criticise?**
   It has not been run against the live portal, so every upstream path/field is an assumption until verified. The cache and client are per-process and simple. There are no tests for the browser code. `voltR` units are unknown. The detail endpoint relies on search behaviour that may not return every field.
