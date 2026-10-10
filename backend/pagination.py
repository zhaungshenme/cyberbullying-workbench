"""Signed list positions. A cursor narrows a query; it never grants access."""
import base64
import hashlib
import hmac
import json
import math
import re


class PageCursor:
    def __init__(self, backend_token):
        self.key = hmac.new(backend_token.encode(), b"workbench-cursor-v1\0", hashlib.sha256).digest()

    def encode(self, endpoint, actor, high_water, after):
        body = json.dumps({"v": 1, "endpoint": endpoint, "actor": actor["id"], "role": actor["role"],
            "high_water": high_water, "after": after}, separators=(",", ":"), sort_keys=True).encode()
        payload = base64.urlsafe_b64encode(body).rstrip(b"=").decode()
        signature = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def decode(self, value, endpoint, actor):
        try:
            if not isinstance(value, str) or not 1 <= len(value) <= 2048:
                raise ValueError
            payload, signature = value.split(".")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", payload) or not re.fullmatch(r"[a-f0-9]{64}", signature):
                raise ValueError
            expected = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            raw = base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"v", "endpoint", "actor", "role", "high_water", "after"}:
                raise ValueError
            if type(data["v"]) is not int or data["v"] != 1 or (data["endpoint"], data["actor"], data["role"]) != (endpoint, actor["id"], actor["role"]):
                raise ValueError
            if type(data["high_water"]) is not int or not 0 <= data["high_water"] <= 9223372036854775807:
                raise ValueError
            after = data["after"]
            if endpoint == "tasks":
                if not isinstance(after, list) or len(after) != 2 or type(after[0]) not in {int, float} or not math.isfinite(after[0]) or not isinstance(after[1], str) or not 1 <= len(after[1]) <= 200:
                    raise ValueError
            elif endpoint == "audit":
                if type(after) is not int or not 1 <= after <= data["high_water"]:
                    raise ValueError
            else:
                raise ValueError
            return data["high_water"], after
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            raise ValueError("invalid_cursor") from exc
