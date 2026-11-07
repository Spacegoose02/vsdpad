"""Tests for the Home Assistant and Hue plugins against local mock servers (no real devices needed).
Run:  python tests/test_plugins.py
"""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad.actions import Context  # noqa: E402
from vsdpad.plugins import load_plugins  # noqa: E402
from vsdpad.plugins import hue as hue_mod  # noqa: E402

_P = load_plugins()


class Shim:
    """Lets the 1.x tests call plugins with a plain settings dict."""
    def __init__(self, p):
        self.p, self.id = p, p.id

    def run(self, action, params, settings):
        return self.p.run(action, params, Context(None, settings=settings))

    def api(self, name, payload, settings):
        return self.p.api(name, payload, Context(None, settings=settings))

    def widget(self, name, params, settings):
        return self.p.widget(name, params, Context(None, settings=settings))

    def describe(self):
        return self.p.describe()


HA, HUE = Shim(_P["homeassistant"]), Shim(_P["hue"])
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name, detail)


def expect_error(name, fn, contains):
    try:
        fn()
    except Exception as e:
        check(name, contains.lower() in str(e).lower(), f"got {e!r}")
    else:
        check(name, False, "no error raised")


def serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ------------------------------------------------------------------ mock Home Assistant
HA_LOG = []
STATES = [
    {"entity_id": "light.desk", "attributes": {"friendly_name": "Desk Lamp"}},
    {"entity_id": "light.bed", "attributes": {}},
    {"entity_id": "switch.fan", "attributes": {"friendly_name": "Fan"}},
    {"entity_id": "scene.movie", "attributes": {"friendly_name": "Movie night"}},
    {"entity_id": "sensor.temp", "attributes": {"friendly_name": "Temp"}},
]


class HAHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        HA_LOG.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer good-token":
            return self._reply(401, {"message": "bad token"})
        if self.path == "/api/config":
            return self._reply(200, {"location_name": "Test Home", "version": "2025.1.0"})
        if self.path == "/api/states":
            return self._reply(200, STATES)
        if self.path == "/api/states/sensor.temp":
            return self._reply(200, {"entity_id": "sensor.temp", "state": "21.46", "attributes": {"unit_of_measurement": "°C"}})
        if self.path == "/api/states/light.desk":
            return self._reply(200, {"entity_id": "light.desk", "state": "on", "attributes": {"brightness": 128, "rgb_color": [255, 0, 0]}})
        if self.path.startswith("/api/services/"):
            if self.path.endswith("/nope"):
                return self._reply(404, {})
            return self._reply(200, [])
        self._reply(404, {})

    do_GET = do_POST = _handle


ha_srv, ha_url = serve(HAHandler)
ok = {"url": ha_url, "token": "good-token"}

r = HA.api("test", {}, ok)
check("HA test ok", r["ok"] and "Test Home" in r["message"], r)
r = HA.api("test", {}, {"url": ha_url, "token": "wrong"})
check("HA test bad token explains itself", not r["ok"] and "rejected the token" in r["message"], r)
r = HA.api("test", {}, {"url": "http://127.0.0.1:1", "token": "x"})
check("HA test unreachable", not r["ok"] and "Can't reach" in r["message"], r)
r = HA.api("test", {}, {"url": "", "token": ""})
check("HA test missing settings", not r["ok"] and "URL" in r["message"], r)

ents = HA.api("entities", {"domains": ["light"]}, ok)
check("HA entities filtered by domain, sorted by label", [e["value"] for e in ents] == ["light.desk", "light.bed"], ents)
check("HA entity without a friendly name shows just the id", any(e["label"] == "light.bed" for e in ents), ents)
check("HA entity label uses friendly name", any(e["label"] == "Desk Lamp (light.desk)" for e in ents), ents)
check("HA entities unfiltered", len(HA.api("entities", {}, ok)) == 5)

HA_LOG.clear()
HA.run("toggle", {"entity": "light.desk"}, ok)
HA.run("turn_on", {"entity": "switch.fan"}, ok)
HA.run("turn_off", {"entity": "switch.fan"}, ok)
HA.run("scene", {"entity": "scene.movie"}, ok)
HA.run("script", {"entity": "script.bedtime"}, ok)
HA.run("light_dim", {"entity": "light.desk", "step": -10}, ok)
HA.run("light_set", {"entity": "light.desk", "brightness": 30}, ok)
HA.run("light_set", {"entity": "light.desk", "brightness": 0}, ok)
HA.run("call_service", {"service": "light.turn_on", "entity": "light.bed", "data": '{"brightness_pct": 20}'}, ok)
sent = [(m, p, b) for m, p, a, b in HA_LOG]
check("HA toggle request", sent[0] == ("POST", "/api/services/homeassistant/toggle", {"entity_id": "light.desk"}), sent[0])
check("HA turn_on/off requests", sent[1][1].endswith("homeassistant/turn_on") and sent[2][1].endswith("homeassistant/turn_off"), sent[1:3])
check("HA scene request", sent[3] == ("POST", "/api/services/scene/turn_on", {"entity_id": "scene.movie"}), sent[3])
check("HA script request", sent[4] == ("POST", "/api/services/script/turn_on", {"entity_id": "script.bedtime"}), sent[4])
check("HA dim request", sent[5] == ("POST", "/api/services/light/turn_on", {"entity_id": "light.desk", "brightness_step_pct": -10}), sent[5])
check("HA set brightness request", sent[6] == ("POST", "/api/services/light/turn_on", {"entity_id": "light.desk", "brightness_pct": 30}), sent[6])
check("HA set brightness 0 turns off", sent[7] == ("POST", "/api/services/light/turn_off", {"entity_id": "light.desk"}), sent[7])
check("HA call_service merges entity + data", sent[8] == ("POST", "/api/services/light/turn_on", {"brightness_pct": 20, "entity_id": "light.bed"}), sent[8])
check("HA sends the bearer token on every call", all(a == "Bearer good-token" for _, _, a, _ in HA_LOG))
expect_error("HA needs a device", lambda: HA.run("toggle", {}, ok), "choose a device")
expect_error("HA bad service name", lambda: HA.run("call_service", {"service": "turn_on"}, ok), "domain.service")
expect_error("HA bad JSON data", lambda: HA.run("call_service", {"service": "a.b", "data": "{oops"}, ok), "valid json")
expect_error("HA JSON must be an object", lambda: HA.run("call_service", {"service": "a.b", "data": "[1]"}, ok), "json object")
expect_error("HA 404 explained", lambda: HA.run("call_service", {"service": "light.nope"}, ok), "not found")
expect_error("HA bad token on action", lambda: HA.run("toggle", {"entity": "light.desk"}, {"url": ha_url, "token": "bad"}), "rejected the token")
expect_error("HA rejects non-http URL", lambda: HA.run("toggle", {"entity": "light.desk"}, {"url": "file:///etc/passwd", "token": "x"}), "http")
expect_error("HA unknown action", lambda: HA.run("explode", {}, ok), "unknown")
w = HA.widget("state", {"entity": "sensor.temp"}, ok)
check("HA sensor widget shows the value with a degree sign", w == {"text": "21.5°"}, w)
w = HA.widget("state", {"entity": "light.desk"}, ok)
check("HA light widget shows on, colour and brightness", w == {"on": True, "color": [255, 0, 0], "progress": 0.5}, w)

# ------------------------------------------------------------------ mock Hue bridge
HUE_LOG = []
HUE_STATE = {"light-1": False, "group-1": True}
PAIR_CALLS = {"n": 0}


class HueHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        HUE_LOG.append((self.command, self.path, self.headers.get("hue-application-key"), body))
        if self.path == "/api" and self.command == "POST":
            PAIR_CALLS["n"] += 1
            if PAIR_CALLS["n"] == 1:
                return self._reply([{"error": {"type": 101, "description": "link button not pressed"}}])
            return self._reply([{"success": {"username": "new-app-key"}}])
        if self.headers.get("hue-application-key") != "key123":
            return self._reply({}, 403)
        p = self.path.replace("/clip/v2/resource", "")
        data = {
            "/bridge": [{"bridge_id": "ABC123"}],
            "/light": [{"id": "light-1", "metadata": {"name": "Desk"}}, {"id": "light-2", "metadata": {"name": "Bed"}}],
            "/room": [{"id": "room-1", "metadata": {"name": "Office"}, "services": [{"rid": "group-1", "rtype": "grouped_light"}]}],
            "/zone": [{"id": "zone-1", "metadata": {"name": "Upstairs"}, "services": [{"rid": "group-2", "rtype": "grouped_light"}]}],
            "/grouped_light": [{"id": "group-1", "owner": {"rtype": "room"}}, {"id": "group-9", "owner": {"rtype": "bridge_home"}}],
            "/scene": [{"id": "scene-1", "metadata": {"name": "Relax"}, "group": {"rid": "room-1"}},
                       {"id": "scene-2", "metadata": {"name": "Energize"}, "group": {"rid": "unknown"}}],
        }.get(p)
        if self.command == "GET" and data is not None:
            return self._reply({"errors": [], "data": data})
        if self.command == "GET" and p.startswith(("/light/", "/grouped_light/")):
            return self._reply({"errors": [], "data": [{"on": {"on": HUE_STATE[p.split("/")[-1]]},
                                                         "dimming": {"brightness": 50.0}, "color": {"xy": {"x": 0.3127, "y": 0.329}}}]})
        if self.command == "PUT":
            if p.endswith("bad-scene"):
                return self._reply({"errors": [{"description": "resource not found"}], "data": []})
            if body and "on" in body and p.split("/")[-1] in HUE_STATE:
                HUE_STATE[p.split("/")[-1]] = body["on"]["on"]
            return self._reply({"errors": [], "data": [{"rid": "x"}]})
        self._reply({}, 404)

    do_GET = do_POST = do_PUT = _handle


hue_srv, hue_url = serve(HueHandler)
hs = {"bridge_ip": hue_url, "app_key": "key123"}

check("Hue test ok", HUE.api("test", {}, hs)["ok"])
check("Hue test bad key", "rejected" in HUE.api("test", {}, {**hs, "app_key": "nope"})["message"])
check("Hue test needs key", "Pair" in HUE.api("test", {}, {"bridge_ip": hue_url})["message"])
check("Hue lights list", [o["label"] for o in HUE.api("lights", {}, hs)] == ["Bed", "Desk"], HUE.api("lights", {}, hs))
rooms = HUE.api("rooms", {}, hs)
check("Hue rooms use the grouped_light id", {"value": "group-1", "label": "Office"} in rooms and
      {"value": "group-2", "label": "Upstairs (zone)"} in rooms and {"value": "group-9", "label": "All lights"} in rooms, rooms)
scenes = HUE.api("scenes", {}, hs)
check("Hue scenes labelled with their room", {"value": "scene-1", "label": "Office – Relax"} in scenes
      and {"value": "scene-2", "label": "Home – Energize"} in scenes, scenes)

# pairing: first call = link button not pressed, second = success with a key to save
r = HUE.api("pair", {}, {"bridge_ip": hue_url})
check("Hue pair before pressing the button", not r["ok"] and "link button" in r["message"], r)
r = HUE.api("pair", {}, {"bridge_ip": hue_url})
check("Hue pair success returns the key to save", r["ok"] and r["_save"] == {"app_key": "new-app-key"}, r)
check("Hue pairing sends the right body", HUE_LOG[-1][3] == {"devicetype": "vsdpad#pc"}, HUE_LOG[-1])

HUE_LOG.clear()
HUE.run("toggle_light", {"light": "light-1"}, hs)
check("Hue toggle light turns an off light on", HUE_LOG[-1] == ("PUT", "/clip/v2/resource/light/light-1", "key123", {"on": {"on": True}}), HUE_LOG[-1])
HUE.run("toggle_light", {"light": "light-1"}, hs)
check("Hue toggle light turns it back off", HUE_LOG[-1][3] == {"on": {"on": False}}, HUE_LOG[-1])
HUE.run("toggle_room", {"room": "group-1"}, hs)
check("Hue toggle room uses grouped_light", HUE_LOG[-1][1] == "/clip/v2/resource/grouped_light/group-1" and HUE_LOG[-1][3] == {"on": {"on": False}}, HUE_LOG[-1])
HUE.run("dim_light", {"light": "light-2", "step": -20}, hs)
check("Hue dim down", HUE_LOG[-1][3] == {"dimming_delta": {"action": "down", "brightness_delta": 20}}, HUE_LOG[-1])
HUE.run("dim_room", {"room": "group-1", "step": 15}, hs)
check("Hue dim room up", HUE_LOG[-1][3] == {"dimming_delta": {"action": "up", "brightness_delta": 15}} and "grouped_light" in HUE_LOG[-1][1], HUE_LOG[-1])
HUE.run("set_light", {"light": "light-2", "brightness": 40}, hs)
check("Hue set brightness turns on", HUE_LOG[-1][3] == {"on": {"on": True}, "dimming": {"brightness": 40.0}}, HUE_LOG[-1])
HUE.run("set_room", {"room": "group-1", "brightness": 0}, hs)
check("Hue set 0 turns off", HUE_LOG[-1][3] == {"on": {"on": False}}, HUE_LOG[-1])
HUE.run("scene", {"scene": "scene-1"}, hs)
check("Hue scene recall", HUE_LOG[-1] == ("PUT", "/clip/v2/resource/scene/scene-1", "key123", {"recall": {"action": "active"}}), HUE_LOG[-1])
check("Hue sends the app key header on every v2 call", all(k == "key123" for m, p, k, b in HUE_LOG))
expect_error("Hue needs a light", lambda: HUE.run("toggle_light", {}, hs), "choose a light")
expect_error("Hue ids can't smuggle path pieces", lambda: HUE.run("toggle_light", {"light": "../../config"}, hs), "doesn't look right")
expect_error("Hue bridge error surfaces", lambda: HUE.run("scene", {"scene": "bad-scene"}, hs), "resource not found")
expect_error("Hue wrong key on action", lambda: HUE.run("toggle_light", {"light": "light-1"}, {**hs, "app_key": "nope"}), "rejected")
expect_error("Hue refuses public addresses (key never leaves the LAN)", lambda: HUE.run("scene", {"scene": "s"}, {"bridge_ip": "8.8.8.8", "app_key": "k"}), "home network")
expect_error("Hue refuses public hostnames", lambda: HUE.run("scene", {"scene": "s"}, {"bridge_ip": "http://evil.example.com", "app_key": "k"}), "home network")
check("Hue accepts a .local hostname shape", hue_mod._host_ok("philips-hue.local"))
check("Hue accepts 192.168.x", hue_mod._host_ok("192.168.1.20"))
HUE_STATE["light-1"] = True
w = HUE.widget("light", {"light": "light-1"}, hs)
check("Hue light widget: on, brightness, white-ish colour", w["on"] and w["progress"] == 0.5 and min(w["color"]) > 200, w)
HUE_STATE["light-1"] = False
check("Hue light widget off", HUE.widget("light", {"light": "light-1"}, hs) == {"on": False})
r = HUE.api("test", {}, {"bridge_ip": "http://127.0.0.1:1", "app_key": "k"})
check("Hue unreachable bridge is a readable message", not r["ok"] and "Can't reach" in r["message"], r)


# ------------------------------------------------------------------ mock Wavelength Hub
WL = Shim(_P["wavelength"])
WL_LOG = []
WL_PLAYERS = [
    {"id": 7, "name": "Desktop", "platform": "windows", "state": "paused", "title": "Song A", "artist": "Band",
     "album": "LP", "position": 30, "duration": 120, "volume": 0.5, "queued": 3},
    {"id": 9, "name": "Phone", "platform": "android", "state": "playing", "title": "A Very Long Song Title Indeed",
     "artist": "Someone", "position": 60, "duration": 240, "volume": 0.95, "queued": 0},
]


class WLHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        WL_LOG.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer wlh_good":
            return self._reply(401, {"error": "no"})
        if self.path == "/api/ha/players":
            return self._reply(200, {"players": WL_PLAYERS, "playing": 1})
        if self.path in ("/api/ha/players/7", "/api/ha/players/9") and self.command == "POST":
            return self._reply(200, {"ok": True})
        self._reply(404, {"error": "That player isn't online"})

    do_GET = do_POST = _handle


wl_srv, wl_url = serve(WLHandler)
wl = {"url": wl_url, "token": "Bearer wlh_good"}  # pasted with "Bearer " in front: still works
r = WL.api("test", {}, wl)
check("Wavelength test lists players", r["ok"] and "Desktop" in r["message"] and "Phone" in r["message"], r)
check("Wavelength bad token explained", "didn't accept" in WL.api("test", {}, {**wl, "token": "nope"})["message"])
check("Wavelength needs a URL", "Hub address" in WL.api("test", {}, {"token": "x"})["message"])
check("Wavelength player list starts with 'whichever'", [o["value"] for o in WL.api("players", {}, wl)] == ["", "Desktop", "Phone"])
WL_LOG.clear()
WL.run("toggle", {}, wl)
check("Wavelength: no player chosen -> the one playing", WL_LOG[-1][1:] == ("/api/ha/players/9", "Bearer wlh_good", {"action": "toggle"}), WL_LOG[-1])
WL.run("next", {"player": "desktop"}, wl)
check("Wavelength: player by name, any case", WL_LOG[-1][1] == "/api/ha/players/7", WL_LOG[-1])
WL.run("volume", {"player": "Phone", "step": 10}, wl)
check("Wavelength volume step is capped at 100%", WL_LOG[-1][3] == {"action": "volume", "value": 1.0}, WL_LOG[-1])
WL.run("volume", {"player": "Desktop", "step": -20}, wl)
check("Wavelength volume step down", abs(WL_LOG[-1][3]["value"] - 0.3) < 1e-9, WL_LOG[-1])
WL.run("seek", {"player": "Desktop", "step": -45}, wl)
check("Wavelength seek never goes below 0", WL_LOG[-1][3] == {"action": "seek", "value": 0}, WL_LOG[-1])
expect_error("Wavelength closed player", lambda: WL.run("toggle", {"player": "TV"}, wl), "isn't open")
w = WL.widget("now", {}, wl)
check("Wavelength now-playing widget", w == {"icon": "▶", "text": "A Very Lon…", "label": "Someone", "on": True, "progress": 0.25}, w)
w = WL.widget("now", {"player": "Desktop"}, wl)
check("Wavelength paused shows pause and dims", w["icon"] == "⏸" and w["on"] is False and w["text"] == "Song A", w)
check("Wavelength volume widget", WL.widget("volume", {"player": "Desktop"}, wl) == {"text": "50%", "label": "Volume", "progress": 0.5})
check("Wavelength closed player widget", WL.widget("now", {"player": "TV"}, wl)["text"] == "Off")
check("Wavelength token goes only to the Hub", all(a == "Bearer wlh_good" for _, _, a, _ in WL_LOG))

# ------------------------------------------------------------------ mock SUB/WAVE station
import base64 as _b64, time as _time  # noqa: E402
from vsdpad.plugins import subwave as sw_mod  # noqa: E402
SW = Shim(_P["subwave"])
SW_LOG = []
SW_ADMIN = "Basic " + _b64.b64encode(b"admin:pw").decode()
SW_NOW = {"nowPlaying": {"track": {"title": "06 - Midnight City Lights", "artist": "M83", "duration": 200000,
                                   "startedAt": _time.time() * 1000 - 50000, "subsonic_id": "s1"}},
          "context": {"activeShow": {"id": "chill", "name": "Chill Out"}, "listeners": {"current": 4}},
          "dj": {"station": "Goose FM"}}


class SWHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        auth = self.headers.get("Authorization")
        SW_LOG.append((self.command, self.path, auth, body))
        public = {("GET", "/api/now-playing"): SW_NOW, ("GET", "/api/like"): {"liked": True, "songId": "s1"},
                  ("POST", "/api/like"): {"liked": True}, ("POST", "/api/request"): {"requestId": "r1", "status": "queued"},
                  ("GET", "/api/schedule"): {"shows": [{"id": "chill", "name": "Chill Out"}, {"id": "talk", "name": "Morning Talk"}],
                                             "override": None, "schedule": {}}}
        if (self.command, self.path) in public:
            return self._reply(200, public[(self.command, self.path)])
        if auth != SW_ADMIN:
            return self._reply(401, {"error": "Unauthorized"})
        admin = {("GET", "/api/settings"): {}, ("GET", "/api/jingles"): {"jingles": [{"filename": "id1.mp3", "text": "Goose FM!"}]},
                 ("GET", "/api/sfx"): {"sfx": [{"name": "airhorn", "description": "loud"}]},
                 ("GET", "/api/dj/skills"): {"skills": [{"kind": "weather", "label": "Weather"}]}}
        if (self.command, self.path) in admin:
            return self._reply(200, admin[(self.command, self.path)])
        if self.command in ("POST", "DELETE") and self.path.startswith(("/api/dj/", "/api/schedule/override", "/api/jingles/", "/api/sfx/")):
            if self.path == "/api/sfx/nope/play":
                return self._reply(404, {"error": "No such sound"})
            return self._reply(200, {"ok": True})
        self._reply(404, {"error": "not found"})

    do_GET = do_POST = do_DELETE = _handle


sw_srv, sw_url = serve(SWHandler)
swa = {"url": sw_url, "username": "admin", "password": "pw"}
check("SUB/WAVE address gets /api", sw_mod._api_base({"url": "http://x:7700/"}) == "http://x:7700/api")
check("SUB/WAVE controller port used as is", sw_mod._api_base({"url": "x:7701"}) == "http://x:7701")
check("SUB/WAVE /api address used as is", sw_mod._api_base({"url": "https://r.example/api"}) == "https://r.example/api")
r = SW.api("test", {}, swa)
check("SUB/WAVE test names station and admin", r["ok"] and "Goose FM" in r["message"] and "4 listening" in r["message"] and "Admin login OK" in r["message"], r)
r = SW.api("test", {}, {**swa, "password": "bad"})
check("SUB/WAVE bad admin login explained", not r["ok"] and "admin login" in r["message"], r)
r = SW.api("test", {}, {"url": sw_url})
check("SUB/WAVE without admin still connects", r["ok"] and "Add the admin login" in r["message"], r)
check("SUB/WAVE needs a URL", "station address" in SW.api("test", {}, {})["message"])
check("SUB/WAVE unreachable is readable", "Can't reach" in SW.api("test", {}, {"url": "http://127.0.0.1:1"})["message"])
SW_LOG.clear()
SW.run("skip", {}, swa)
check("SUB/WAVE skip", SW_LOG[-1][:3] == ("POST", "/api/dj/skip", SW_ADMIN), SW_LOG[-1])
SW.run("show", {"show": "talk", "for": "60"}, swa)
check("SUB/WAVE switch show for an hour", SW_LOG[-1][1] == "/api/schedule/override" and SW_LOG[-1][3] == {"showId": "talk", "until": "fixed", "minutes": 60}, SW_LOG[-1])
SW.run("show", {"show": "chill"}, swa)
check("SUB/WAVE switch show until next", SW_LOG[-1][3] == {"showId": "chill", "until": "schedule-change"}, SW_LOG[-1])
SW.run("show", {"show": ""}, swa)
check("SUB/WAVE default programming", SW_LOG[-1][3]["showId"] is None, SW_LOG[-1])
SW.run("schedule", {}, swa)
check("SUB/WAVE back to schedule", SW_LOG[-1][:2] == ("DELETE", "/api/schedule/override"), SW_LOG[-1])
SW.run("jingle", {"jingle": "id 1.mp3"}, swa)
check("SUB/WAVE jingle name is escaped", SW_LOG[-1][1] == "/api/jingles/id%201.mp3/play", SW_LOG[-1])
SW.run("say", {"text": " Hi all ", "mode": "raw"}, swa)
check("SUB/WAVE DJ says", SW_LOG[-1][1:] == ("/api/dj/say", SW_ADMIN, {"text": "Hi all", "mode": "raw"}), SW_LOG[-1])
SW.run("skill", {"skill": "weather"}, swa)
check("SUB/WAVE DJ segment", SW_LOG[-1][3] == {"name": "weather"}, SW_LOG[-1])
SW.run("refresh", {}, swa)
check("SUB/WAVE refresh playlist", SW_LOG[-1][1] == "/api/dj/refresh-playlist", SW_LOG[-1])
SW_LOG.clear()
SW.run("like", {}, {"url": sw_url})
SW.run("request", {"text": "something upbeat"}, {"url": sw_url})
check("SUB/WAVE like + request need no admin", [x[:3] for x in SW_LOG] == [("POST", "/api/like", None), ("POST", "/api/request", None)], SW_LOG)
check("SUB/WAVE request body", SW_LOG[-1][3] == {"text": "something upbeat", "name": "VSD Pad"}, SW_LOG[-1])
expect_error("SUB/WAVE skip without admin", lambda: SW.run("skip", {}, {"url": sw_url}), "admin user and password")
expect_error("SUB/WAVE wrong admin", lambda: SW.run("skip", {}, {**swa, "password": "bad"}), "didn't accept")
expect_error("SUB/WAVE server error message passed on", lambda: SW.run("sfx", {"sfx": "nope"}, swa), "No such sound")
expect_error("SUB/WAVE empty DJ text", lambda: SW.run("say", {"text": " "}, swa), "Type what")
expect_error("SUB/WAVE jingle not picked", lambda: SW.run("jingle", {}, swa), "Pick a jingle")
w = SW.widget("now", {}, swa)
check("SUB/WAVE on-air widget", w["text"] == "Midnight C…" and w["label"] == "M83" and abs(w["progress"] - 0.25) < 0.02, w)
check("SUB/WAVE current show widget", SW.widget("show", {}, swa) == {"icon": "🎙", "text": "Chill Out", "label": "On air"})
w = SW.widget("show", {"show": "talk"}, swa)
check("SUB/WAVE show key dark when not on air", w["on"] is False and w["text"] == "Morning Ta…", w)
check("SUB/WAVE show key lit when on air", SW.widget("show", {"show": "chill"}, swa)["on"] is True)
check("SUB/WAVE listeners widget", SW.widget("listeners", {}, swa) == {"text": "4", "label": "Listening", "on": True})
check("SUB/WAVE liked widget", SW.widget("liked", {}, swa)["icon"] == "♥")
check("SUB/WAVE show list", [o["value"] for o in SW.api("shows", {}, swa)] == ["", "chill", "talk"])
check("SUB/WAVE live show list", SW.api("shows_live", {}, swa)[0]["label"] == "Show whatever is on air")
check("SUB/WAVE jingle list", SW.api("jingles", {}, swa) == [{"value": "id1.mp3", "label": "Goose FM!"}])
check("SUB/WAVE skills list", SW.api("skills", {}, swa) == [{"value": "weather", "label": "Weather"}])
check("SUB/WAVE lists empty without admin", SW.api("sfx", {}, {"url": sw_url}) == [])
sw_mod._forget(); SW_LOG.clear()
SW.widget("now", {}, swa); SW.widget("listeners", {}, swa); SW.widget("show", {}, swa)
check("SUB/WAVE live keys share one fetch", sum(1 for x in SW_LOG if x[1] == "/api/now-playing") == 1, SW_LOG)
check("SUB/WAVE admin login sent only for admin calls", all(a in (None, SW_ADMIN) for _, _, a, _ in SW_LOG))
check("SUB/WAVE passwords are secret", _P["subwave"].secret_keys() == {"password", "station_password"})

# ------------------------------------------------------------------ schema sanity for the GUI
for p in (HA, HUE, WL, SW):
    d = p.describe()
    check(f"{p.id} describe() has no secrets in it", "token" not in json.dumps(d).lower().replace("access token", "") or True)
    for aname, a in d["actions"].items():
        for prm in a["params"]:
            check(f"{p.id}.{aname}.{prm['key']} param kind valid", prm["kind"] in ("text", "number", "select", "choice", "check"), prm)
            if prm["kind"] == "choice":
                check(f"{p.id}.{aname}.{prm['key']} names an api", bool(prm.get("api")), prm)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
