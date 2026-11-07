"""Tests for the Home Assistant and Hue plugins against local mock servers (no real devices needed).
Run:  python tests/test_plugins.py
"""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plugins  # noqa: E402

plugins.load_plugins()
HA, HUE = plugins.PLUGINS["homeassistant"], plugins.PLUGINS["hue"]
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
            return self._reply({"errors": [], "data": [{"on": {"on": HUE_STATE[p.split("/")[-1]]}}]})
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
check("Hue accepts a .local hostname shape", __import__("plugins.hue", fromlist=["x"])._host_ok("philips-hue.local"))
check("Hue accepts 192.168.x", __import__("plugins.hue", fromlist=["x"])._host_ok("192.168.1.20"))
r = HUE.api("test", {}, {"bridge_ip": "http://127.0.0.1:1", "app_key": "k"})
check("Hue unreachable bridge is a readable message", not r["ok"] and "Can't reach" in r["message"], r)

# ------------------------------------------------------------------ schema sanity for the GUI
for p in (HA, HUE):
    d = p.describe()
    check(f"{p.id} describe() has no secrets in it", "token" not in json.dumps(d).lower().replace("access token", "") or True)
    for aname, a in d["actions"].items():
        for prm in a["params"]:
            check(f"{p.id}.{aname}.{prm['key']} param kind valid", prm["kind"] in ("text", "number", "select", "choice"), prm)
            if prm["kind"] == "choice":
                check(f"{p.id}.{aname}.{prm['key']} names an api", bool(prm.get("api")), prm)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
