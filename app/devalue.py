"""Decode SvelteKit's compact `__data.json` payloads ("devalue" format).

The portal is a SvelteKit app. Its page-data routes return a FLAT list in which objects and
arrays refer to other entries by index, instead of nesting values directly. This turns that
list back into normal Python dicts/lists. Only what the portal is known to use is handled;
unknown tagged values are kept as {"_tag": ..., "_value": ...} rather than guessed.
"""

_SPECIAL = {-1: None, -2: "NaN", -3: "Infinity", -4: "-Infinity", -5: -0.0}  # undefined, NaN, +Inf, -Inf, -0


def unflatten(data, root=0):
    memo = {}

    def get(i):
        if not isinstance(i, int) or isinstance(i, bool):
            raise ValueError("bad reference in __data.json payload")
        if i in _SPECIAL:
            return _SPECIAL[i]
        if i < 0 or i >= len(data):
            raise ValueError("reference out of range in __data.json payload")
        if i in memo:
            return memo[i]
        value = data[i]
        if isinstance(value, list):
            if value and isinstance(value[0], str):  # tagged value, e.g. ["Date", "2026-..."]
                tag, rest = value[0], value[1:]
                if tag in ("Date", "BigInt"):
                    out = rest[0] if rest else None
                elif tag in ("Set", "Map"):
                    out = [get(x) for x in rest]
                else:
                    out = {"_tag": tag, "_value": rest}
                memo[i] = out
                return out
            out = []
            memo[i] = out  # register before recursing so shared/cyclic references work
            out.extend(get(x) for x in value)
            return out
        if isinstance(value, dict):
            out = {}
            memo[i] = out
            for key, ref in value.items():
                out[key] = get(ref)
            return out
        memo[i] = value  # plain string / number / bool / null
        return value

    try:
        return get(root)
    except RecursionError as exc:  # absurdly deep payload: fail cleanly, not with a 500
        raise ValueError("__data.json payload is nested too deeply") from exc