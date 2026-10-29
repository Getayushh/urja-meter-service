"""Small, polite probes of the Urja portal's behaviour. Run from the project root:

    python scripts/probe_portal.py                 # tests 1-3 (about 8 requests)
    python scripts/probe_portal.py --expiry-minutes 61   # also test REAL session expiry

Uses PORTAL_* values from .env. Prints only status codes, content types and short
non-secret hints. Never prints cookies, tokens or credentials. Read-only except for
normal logins. It deliberately does NOT probe rate limits.
"""
import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("PORTAL_BASE_URL", "https://urja-ops.flockenergy.tech").rstrip("/")
LOGIN = BASE + os.getenv("PORTAL_LOGIN_PATH", "/login")
USER, PASS = os.getenv("PORTAL_USERNAME"), os.getenv("PORTAL_PASSWORD")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
DATA_URLS = ["/portal/dts?page=1", "/portal/meters/search?q=&page=1"]
RATE_HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "ratelimit")
PAUSE = 2  # seconds between requests


def describe(label, r):
    ctype = r.headers.get("Content-Type", "-").split(";")[0]
    extra = []
    if r.headers.get("Location"):
        extra.append("Location=" + r.headers["Location"][:60])
    for h in RATE_HEADERS:
        if h in {k.lower() for k in r.headers}:
            extra.append(f"{h}={r.headers.get(h)}")
    print(f"  {label:<34} HTTP {r.status_code}  {ctype}  " + "  ".join(extra))
    return r


def get(session, path, cookies=None):
    time.sleep(PAUSE)
    return session.get(BASE + path, cookies=cookies, allow_redirects=False,
                       headers={"Accept": "application/json", "User-Agent": UA}, timeout=15)


def login(session, variant):
    h = {}
    if "origin" in variant:
        h["Origin"] = BASE
    if "referer" in variant:
        h["Referer"] = LOGIN
    if "action" in variant:
        h["X-Sveltekit-Action"] = "true"
        h["Accept"] = "application/json"
    if "ua" in variant:
        h["User-Agent"] = UA
    time.sleep(PAUSE)
    return session.post(LOGIN, data={"email": USER, "password": PASS}, headers=h,
                        allow_redirects=False, timeout=15)


def body_type(r):
    try:
        return r.json().get("type")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expiry-minutes", type=int, default=0, help="also wait this long and re-use one session")
    args = ap.parse_args()
    if not USER or not PASS:
        sys.exit("Set PORTAL_USERNAME and PORTAL_PASSWORD in .env first.")

    print("\n1) No cookie at all (does the portal require login?)")
    for p in DATA_URLS:
        describe(p, get(requests.Session(), p))

    print("\n2) Invalid session cookie (what an expired session probably looks like)")
    bogus = {"__Secure-better-auth.session_token": "invalid.invalid"}
    for p in DATA_URLS:
        describe(p, get(requests.Session(), p, cookies=bogus))

    print("\n3) First-login 403: which request headers matter? (5 logins, 2 s apart)")
    variants = [("bare (no extra headers)", set()),
                ("origin + referer", {"origin", "referer"}),
                ("origin + referer + action", {"origin", "referer", "action"}),
                ("origin + referer + UA", {"origin", "referer", "ua"}),
                ("all four", {"origin", "referer", "action", "ua"})]
    good = None
    for label, v in variants:
        s = requests.Session()
        r = login(s, v)
        info = f"json type={body_type(r)}" if body_type(r) else "no JSON type"
        describe(label, r)
        print(f"{'':36}{info}; cookie set: {bool(s.cookies)}")
        if r.status_code == 403:
            print(f"{'':36}403 body starts: {r.text[:90]!r}")
        if r.status_code == 429:
            print("  Rate-limited: stopping here."); return
        if good is None and body_type(r) in ("success", "redirect"):
            good = s

    if args.expiry_minutes and good is not None:
        print(f"\n4) Real expiry: waiting {args.expiry_minutes} min with one logged-in session...")
        describe("before waiting", get(good, DATA_URLS[0]))
        for m in range(args.expiry_minutes):
            time.sleep(60)
            if (m + 1) % 10 == 0:
                print(f"  ...{m + 1} min")
        describe("after waiting (same session)", get(good, DATA_URLS[0]))
    print("\nDone. Paste the output (it contains no secrets) back into the chat.")


if __name__ == "__main__":
    main()