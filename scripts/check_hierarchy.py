"""Sample meters and check their hierarchy data for gaps/inconsistencies.

Start the server first (uvicorn app.main:app --port 8000), then:

    python scripts/check_hierarchy.py
    python scripts/check_hierarchy.py J100010 J100011      # your own ids

Uses OUR local API only (which caches and backs off). ~1 request per second, about 25 requests.
Prints no secrets.
"""
import sys
import time

import requests

API = "http://127.0.0.1:8000/api/v1"
DEFAULT_IDS = ["J100000", "J100001", "J100002", "J100004", "J100005", "J100020", "J100100",
               "J100200", "J100234", "J100300", "J100399", "J100402"]
LEVELS = ["zone", "circle", "division", "subdivision", "substation", "feeder", "transformer"]


def get(path):
    time.sleep(1)
    return requests.get(API + path, timeout=30)


def main():
    ids = sys.argv[1:] or DEFAULT_IDS
    print(f"{'meter':<9}{'status':<15}{'type':<14}{'levels':<8}{'dt matches search':<19}notes")
    for mid in ids:
        h = get(f"/meters/{mid}/hierarchy")
        if h.status_code != 200:
            print(f"{mid:<9}HTTP {h.status_code}: {h.json().get('error', '?')}")
            continue
        h = h.json()
        m = get(f"/meters/{mid}")
        dt_search = m.json().get("dtCode") if m.status_code == 200 else None
        got = {lv["level"]: lv for lv in h["hierarchy"]}
        dt_h = got.get("transformer", {}).get("code")
        notes = []
        missing = [x for x in LEVELS if x not in got]
        if missing:
            notes.append("missing: " + ",".join(missing))
        nocode = [x for x, lv in got.items() if not lv["code"]]
        if nocode:
            notes.append("no code: " + ",".join(nocode))
        if m.status_code != 200:
            notes.append(f"search lookup HTTP {m.status_code}")
        match = "n/a" if not (dt_h and dt_search) else ("yes" if dt_h == dt_search else f"NO ({dt_h} vs {dt_search})")
        print(f"{mid:<9}{str(h['installationStatus']):<15}{str(h['installationType']):<14}"
              f"{len(h['hierarchy'])}/7    {match:<19}{'; '.join(notes)}")

    print("\nA meter id that should not exist:")
    r = get("/meters/J999999/hierarchy")
    print(f"  J999999 -> HTTP {r.status_code}  {r.text[:120]}")


if __name__ == "__main__":
    main()
