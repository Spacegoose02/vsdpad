"""Philips Hue plugin (local bridge, CLIP API v2).

Pairing: press the round link button on the bridge, then call pair within ~30 s. The bridge uses a
self-signed certificate, so TLS verification is skipped, but ONLY for private-network addresses
(the app key is never sent to a public host).
"""
import ipaddress, json, ssl, urllib.error, urllib.parse, urllib.request

from .base import Plugin

TIMEOUT = 6
_CTX = ssl._create_unverified_context()


class HueError(Exception):
    pass


def _host_ok(host):
    """Private/loopback IPs, or .local/.lan/.home names (mDNS style). Never a public address."""
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return host.lower().endswith((".local", ".lan", ".home"))


def _base(settings, scheme="https"):
    raw = (settings.get("bridge_ip") or "").strip().rstrip("/")
    if not raw:
        raise HueError("Set the bridge IP in Plugins → Philips Hue (or click Find bridge).")
    if "://" not in raw:
        raw = f"{scheme}://{raw}"
    u = urllib.parse.urlparse(raw)
    if u.scheme not in ("http", "https") or not u.hostname or not _host_ok(u.hostname):
        raise HueError("The bridge address must be on your home network (e.g. 192.168.1.20).")
    return raw


def _open(req, ctx=_CTX):
    """ctx defaults to the bridge's self-signed cert (private addresses only); public sites must pass a verifying context."""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HueError("The Hue bridge rejected the app key. Click Pair and try again.") from None
        raise HueError(f"Hue bridge error {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HueError(f"Can't reach the Hue bridge ({getattr(e, 'reason', e)})") from None
    return json.loads(raw) if raw else None


def _v2(settings, method, path, body=None):
    key = (settings.get("app_key") or "").strip()
    if not key:
        raise HueError("Pair the bridge first: press its link button, then click Pair in Plugins → Philips Hue.")
    req = urllib.request.Request(_base(settings) + "/clip/v2/resource" + path,
                                 data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"hue-application-key": key, "Content-Type": "application/json"})
    res = _open(req)
    if res and res.get("errors"):
        raise HueError("; ".join(e.get("description", "error") for e in res["errors"]))
    return (res or {}).get("data", [])


def _name(item):
    return ((item.get("metadata") or {}).get("name")) or item.get("id", "?")


def _owner_group(item):
    """The grouped_light id that controls a room or zone."""
    for s in item.get("services", []):
        if s.get("rtype") == "grouped_light":
            return s["rid"]
    return None


def _param(key, label, api):
    return {"key": key, "label": label, "kind": "choice", "api": api}


STEP = {"key": "step", "label": "Change (%)", "kind": "number", "default": 15,
        "help": "Positive brightens, negative dims, e.g. 15 or -15"}
LEVEL = {"key": "brightness", "label": "Brightness (%)", "kind": "number", "default": 50}


class Hue(Plugin):
    id = "hue"
    name = "Philips Hue"
    description = "Control Hue lights, rooms and scenes through your Hue bridge on the local network."
    settings = [
        {"key": "bridge_ip", "label": "Bridge IP address", "placeholder": "192.168.1.20"},
        {"key": "app_key", "label": "App key", "secret": True,
         "help": "Filled in automatically when you click Pair."},
    ]
    buttons = [
        {"id": "discover", "label": "Find bridge", "api": "discover"},
        {"id": "pair", "label": "Pair (press the bridge button first)", "api": "pair"},
        {"id": "test", "label": "Test connection", "api": "test"},
    ]
    widgets = {
        "light": {"label": "Light on/off and colour", "interval": 3, "params": [_param("light", "Light", "lights")],
                  "sample": {"on": True, "color": [255, 190, 120], "progress": 0.7}},
        "room": {"label": "Room on/off", "interval": 3, "params": [_param("room", "Room / zone", "rooms")],
                 "sample": {"on": True, "progress": 0.7}},
    }
    actions = {
        "toggle_light": {"label": "Toggle a light", "params": [_param("light", "Light", "lights")]},
        "toggle_room": {"label": "Toggle a room or zone", "params": [_param("room", "Room / zone", "rooms")]},
        "dim_light": {"label": "Dim / brighten a light", "params": [_param("light", "Light", "lights"), STEP]},
        "dim_room": {"label": "Dim / brighten a room", "params": [_param("room", "Room / zone", "rooms"), STEP]},
        "set_light": {"label": "Set light brightness", "params": [_param("light", "Light", "lights"), LEVEL]},
        "set_room": {"label": "Set room brightness", "params": [_param("room", "Room / zone", "rooms"), LEVEL]},
        "scene": {"label": "Activate a scene", "params": [_param("scene", "Scene", "scenes")]},
    }

    # ---- actions -------------------------------------------------------------------------
    def run(self, action, params, ctx):
        settings = ctx.settings
        if action in ("toggle_light", "dim_light", "set_light"):
            path = "/light/" + self._need(params.get("light"), "a light")
        elif action in ("toggle_room", "dim_room", "set_room"):
            path = "/grouped_light/" + self._need(params.get("room"), "a room")
        elif action == "scene":
            _v2(settings, "PUT", "/scene/" + self._need(params.get("scene"), "a scene"), {"recall": {"action": "active"}})
            return
        else:
            raise ValueError(f"unknown Hue action {action!r}")
        if action.startswith("toggle"):
            cur = _v2(settings, "GET", path)[0].get("on", {}).get("on", False)
            _v2(settings, "PUT", path, {"on": {"on": not cur}})
        elif action.startswith("dim"):
            step = max(-100, min(100, int(params.get("step", 15))))
            if step:
                _v2(settings, "PUT", path, {"dimming_delta": {"action": "up" if step > 0 else "down",
                                                              "brightness_delta": abs(step)}})
        else:  # set brightness
            pct = max(0, min(100, int(params.get("brightness", 50))))
            body = {"on": {"on": True}, "dimming": {"brightness": float(pct)}} if pct else {"on": {"on": False}}
            _v2(settings, "PUT", path, body)

    @staticmethod
    def _need(value, what):
        if not value:
            raise HueError(f"Choose {what} first.")
        if not isinstance(value, str) or not all(c.isalnum() or c == "-" for c in value):
            raise HueError("That id doesn't look right.")  # ids go into a URL path
        return value

    # ---- helper calls for the GUI ----------------------------------------------------------
    def api(self, name, payload, ctx):
        settings = ctx.settings
        try:
            if name == "discover":
                found = _open(urllib.request.Request("https://discovery.meethue.com"), ssl.create_default_context())
                if not found:
                    return {"ok": False, "message": "No bridge found. Enter its IP address manually (see your router)."}
                ip = found[0]["internalipaddress"]
                return {"ok": True, "message": f"Found a bridge at {ip}", "fill": {"bridge_ip": ip}}
            if name == "pair":
                req = urllib.request.Request(_base(settings, "http") + "/api", method="POST",
                                             data=json.dumps({"devicetype": "vsdpad#pc"}).encode(),
                                             headers={"Content-Type": "application/json"})
                res = _open(req)
                item = res[0] if isinstance(res, list) and res else {}
                if "success" in item:
                    return {"ok": True, "message": "Paired with the Hue bridge.", "_save": {"app_key": item["success"]["username"]}}
                if item.get("error", {}).get("type") == 101:
                    return {"ok": False, "message": "Press the round link button on top of the bridge, then click Pair again within 30 seconds."}
                return {"ok": False, "message": item.get("error", {}).get("description", "Pairing failed.")}
            if name == "test":
                info = _v2(settings, "GET", "/bridge")
                return {"ok": True, "message": "Connected to Hue bridge " + (info[0].get("bridge_id", "") if info else "")}
            if name == "lights":
                return sorted(({"value": i["id"], "label": _name(i)} for i in _v2(settings, "GET", "/light")),
                              key=lambda o: o["label"].lower())
            if name == "rooms":
                out = []
                for kind, rtype in (("room", ""), ("zone", " (zone)")):
                    for item in _v2(settings, "GET", "/" + kind):
                        gid = _owner_group(item)
                        if gid:
                            out.append({"value": gid, "label": _name(item) + rtype})
                for g in _v2(settings, "GET", "/grouped_light"):
                    if (g.get("owner") or {}).get("rtype") == "bridge_home":
                        out.append({"value": g["id"], "label": "All lights"})
                return sorted(out, key=lambda o: o["label"].lower())
            if name == "scenes":
                groups = {}
                for kind in ("room", "zone"):
                    for item in _v2(settings, "GET", "/" + kind):
                        groups[item["id"]] = _name(item)
                out = [{"value": s["id"], "label": f"{groups.get((s.get('group') or {}).get('rid'), 'Home')} – {_name(s)}"}
                       for s in _v2(settings, "GET", "/scene")]
                return sorted(out, key=lambda o: o["label"].lower())
        except HueError as e:
            if name in ("discover", "pair", "test"):
                return {"ok": False, "message": str(e)}
            raise
        return super().api(name, payload, ctx)


    def widget(self, name, params, ctx):
        if name == "light":
            item = (_v2(ctx.settings, "GET", "/light/" + self._need(params.get("light"), "a light")) or [{}])[0]
        elif name == "room":
            item = (_v2(ctx.settings, "GET", "/grouped_light/" + self._need(params.get("room"), "a room")) or [{}])[0]
        else:
            return super().widget(name, params, ctx)
        on = bool((item.get("on") or {}).get("on"))
        out = {"on": on}
        bri = (item.get("dimming") or {}).get("brightness")
        if on and bri is not None:
            out["progress"] = round(float(bri) / 100, 2)
        xy = ((item.get("color") or {}).get("xy") or {})
        if on and "x" in xy and "y" in xy:
            out["color"] = xy_to_rgb(xy["x"], xy["y"])
        return out


def xy_to_rgb(x, y):
    """CIE xy (full brightness) -> an sRGB colour for the key."""
    y = max(y, 1e-6)
    Y = 1.0
    X, Z = (Y / y) * x, (Y / y) * (1 - x - y)
    r = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b = X * 0.051713 - Y * 0.121364 + Z * 1.011530
    rgb = [max(0.0, c) for c in (r, g, b)]
    m = max(rgb) or 1.0
    rgb = [c / m for c in rgb]
    rgb = [12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055 for c in rgb]
    return [int(max(0, min(1, c)) * 255 + 0.5) for c in rgb]


PLUGIN = Hue()
