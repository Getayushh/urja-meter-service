"""Fetch one SvelteKit __data.json route and print it as readable JSON (structure + truncated values).

    python scripts/inspect_data_json.py "/meters/J100020/__data.json"

One authenticated GET. Prints no cookies or credentials. Check the output for
anything personal before pasting it anywhere.
"""
import json, os, sys, time
import requests
from dotenv import load_dotenv

load_dotenv()
BASE = os.getenv("PORTAL_BASE_URL", "https://urja-ops.flockenergy.tech").rstrip("/")
LOGIN = BASE + os.getenv("PORTAL_LOGIN_PATH", "/login")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


def unflatten(data):
    """Decode SvelteKit 'devalue': a flat list where values point to each other by index."""
    memo = {}
    def get(i):
        if i in (-1, -2, -3, -4, -5):          # undefined, NaN, +Inf, -Inf, -0
            return {-1: None, -2: "NaN", -3: "Infinity", -4: "-Infinity", -5: -0.0}[i]
        if i in memo:
            return memo[i]
        v = data[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):   # tagged value: Date, Set, Map, BigInt, ...
                tag, rest = v[0], v[1:]
                out = {"Date": rest[0] if rest else None, "BigInt": rest[0] if rest else None,
                       "Set": [get(x) for x in rest], "Map": [get(x) for x in rest]}.get(tag, {"_tag": tag, "_value": rest})
                memo[i] = out
                return out
            out = []
            memo[i] = out
            out.extend(get(x) for x in v)
            return out
        if isinstance(v, dict):
            out = {}
            memo[i] = out
            for k, x in v.items():
                out[k] = get(x)
            return out
        memo[i] = v
        return v
    return get(0)


def shorten(x, depth=0):
    if isinstance(x, dict):
        return {k: shorten(v, depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        head = [shorten(v, depth + 1) for v in x[:3]]
        return head + [f"... ({len(x)} items total)"] if len(x) > 3 else head
    if isinstance(x, str) and len(x) > 80:
        return x[:80] + "..."
    return x


def main():
    if len(sys.argv) != 2:
        sys.exit('usage: python scripts/inspect_data_json.py "/path/__data.json?query"')
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.post(LOGIN, data={"email": os.getenv("PORTAL_USERNAME"), "password": os.getenv("PORTAL_PASSWORD")},
               headers={"Origin": BASE, "Referer": LOGIN}, allow_redirects=False, timeout=15)
    if r.status_code != 200 or r.json().get("type") not in ("success", "redirect"):
        sys.exit(f"login failed: HTTP {r.status_code}")
    time.sleep(1)
    r = s.get(BASE + sys.argv[1], headers={"Accept": "application/json"}, allow_redirects=False, timeout=20)
    print(f"HTTP {r.status_code}  {r.headers.get('Content-Type', '-')}  {len(r.content)} bytes")
    try:
        body = r.json()
    except ValueError:
        sys.exit("Not JSON. First 120 chars: " + r.text[:120])
    nodes = body.get("nodes", [])
    print(f"type={body.get('type')}  nodes={len(nodes)}")
    for n, node in enumerate(nodes):
        if not node or node.get("type") != "data":
            print(f"\n--- node {n}: {node.get('type') if node else None}")
            continue
        print(f"\n--- node {n} (decoded, long lists shortened) ---")
        print(json.dumps(shorten(unflatten(node["data"])), indent=2, default=str))


if __name__ == "__main__":
    main()