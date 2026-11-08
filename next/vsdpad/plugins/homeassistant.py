"""Home Assistant plugin (REST API). The token is a long-lived access token created in the user's HA profile.

Only the standard library is used. The token is sent only to the URL the user saved in the settings.
"""
import datetime, json, time, urllib.error, urllib.parse, urllib.request

from .base import Plugin
from . import _ha_live

TIMEOUT = 8
TOGGLE_DOMAINS = ["light", "switch", "fan", "input_boolean", "cover", "media_player", "climate", "lock", "humidifier"]


class HAError(Exception):
    pass


def _base(settings):
    url = (settings.get("url") or "").strip().rstrip("/")
    if not url:
        raise HAError("Set the Home Assistant URL in Plugins → Home Assistant.")
    if "://" not in url:
        url = "http://" + url
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise HAError("The Home Assistant URL must start with http:// or https://")
    return url


def _call(settings, method, path, body=None):
    base = _base(settings)  # checks the URL first: it is the first field on the settings card
    token = (settings.get("token") or "").strip()
    if not token:
        raise HAError("Add your Home Assistant access token in Plugins → Home Assistant.")
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise HAError("Home Assistant rejected the token (401). Create a new long-lived access token.") from None
        if e.code == 404:
            raise HAError(f"Home Assistant said not found (404) for {path} - check the entity or service name.") from None
        raise HAError(f"Home Assistant error {e.code} for {path}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HAError(f"Can't reach Home Assistant at {_base(settings)} ({getattr(e, 'reason', e)})") from None
    return json.loads(raw) if raw else None


def _service(settings, domain, service, data):
    return _call(settings, "POST", f"/api/services/{domain}/{service}", data)


def _entity_param(label, domains=None):
    p = {"key": "entity", "label": label, "kind": "choice", "api": "entities"}
    if domains:
        p["domains"] = domains
    return p


class HomeAssistant(Plugin):
    id = "homeassistant"
    name = "Home Assistant"
    description = "Control lights, switches, scenes and scripts in your Home Assistant."
    settings = [
        {"key": "url", "label": "Home Assistant URL", "placeholder": "http://homeassistant.local:8123"},
        {"key": "token", "label": "Long-lived access token", "secret": True,
         "help": "In Home Assistant: your profile → Security → Long-lived access tokens → Create token. Paste it here."},
    ]
    buttons = [{"id": "test", "label": "Test connection", "api": "test"}]
    widgets = {
        "state": {"label": "Device / sensor state", "interval": 10,
                  "params": [_entity_param("Device or sensor")],
                  "sample": {"text": "21.5°", "on": True}},
        "alert": {"label": "Alert (doorbell, door, motion...)", "interval": 10, "params": [
            _entity_param("Device or sensor"),
            {"key": "state", "label": "Alert when it turns", "kind": "text", "placeholder": "on",
             "help": "e.g. on (doorbell, motion), open (door), home (person). The key stays lit until you clear it."},
            {"key": "name", "label": "Name on the key", "kind": "text", "placeholder": "Doorbell"}],
                  "sample": {"icon": "🔔", "text": "2m ago", "label": "Doorbell", "on": True}},
    }
    actions = {
        "clear_alert": {"label": "Clear an alert key", "params": [_entity_param("Device or sensor")],
                        "help": "Put this on the alert key itself so pressing it clears it."},
        "toggle": {"label": "Toggle a device", "params": [_entity_param("Device", TOGGLE_DOMAINS)]},
        "turn_on": {"label": "Turn a device on", "params": [_entity_param("Device", TOGGLE_DOMAINS)]},
        "turn_off": {"label": "Turn a device off", "params": [_entity_param("Device", TOGGLE_DOMAINS)]},
        "scene": {"label": "Activate a scene", "params": [_entity_param("Scene", ["scene"])]},
        "script": {"label": "Run a script", "params": [_entity_param("Script", ["script"])]},
        "light_dim": {"label": "Dim / brighten a light", "params": [
            _entity_param("Light", ["light"]),
            {"key": "step", "label": "Change (%)", "kind": "number", "default": 10,
             "help": "Positive brightens, negative dims, e.g. 10 or -10"}]},
        "light_set": {"label": "Set light brightness", "params": [
            _entity_param("Light", ["light"]),
            {"key": "brightness", "label": "Brightness (%)", "kind": "number", "default": 50}]},
        "call_service": {"label": "Call any service", "params": [
            {"key": "service", "label": "Service", "kind": "text", "placeholder": "light.turn_on"},
            {"key": "entity", "label": "Entity ID (optional)", "kind": "text", "placeholder": "light.desk"},
            {"key": "data", "label": "Extra data as JSON (optional)", "kind": "text", "placeholder": '{"brightness_pct": 30}'}]},
    }

    def run(self, action, params, ctx):
        settings = ctx.settings
        ent = params.get("entity")
        if action == "clear_alert":
            _ALERT_CLEARED[self._need(ent)] = time.time()
            if getattr(ctx, "app", None) is not None and getattr(ctx.app, "live", None) is not None:
                ctx.app.live.poke("homeassistant")
            return
        if action in ("toggle", "turn_on", "turn_off"):
            _service(settings, "homeassistant", action, {"entity_id": self._need(ent)})
        elif action == "scene":
            _service(settings, "scene", "turn_on", {"entity_id": self._need(ent)})
        elif action == "script":
            _service(settings, "script", "turn_on", {"entity_id": self._need(ent)})
        elif action == "light_dim":
            step = max(-100, min(100, int(params.get("step", 10))))
            _service(settings, "light", "turn_on", {"entity_id": self._need(ent), "brightness_step_pct": step})
        elif action == "light_set":
            pct = max(0, min(100, int(params.get("brightness", 50))))
            _service(settings, "light", "turn_on" if pct else "turn_off",
                     {"entity_id": self._need(ent), "brightness_pct": pct} if pct else {"entity_id": self._need(ent)})
        elif action == "call_service":
            svc = (params.get("service") or "").strip()
            if svc.count(".") != 1 or not all(svc.split(".")):
                raise HAError("Service must look like domain.service, e.g. light.turn_on")
            data = {}
            raw = (params.get("data") or "").strip()
            if raw:
                try:
                    data = json.loads(raw)
                except ValueError:
                    raise HAError("The extra data is not valid JSON.") from None
                if not isinstance(data, dict):
                    raise HAError("The extra data must be a JSON object like {\"brightness_pct\": 30}.")
            if (params.get("entity") or "").strip():
                data["entity_id"] = params["entity"].strip()
            _service(settings, *svc.split("."), data)
        else:
            raise ValueError(f"unknown Home Assistant action {action!r}")

    @staticmethod
    def _need(entity):
        if not entity:
            raise HAError("Choose a device first.")
        return entity

    def api(self, name, payload, ctx):
        settings = ctx.settings
        if name == "test":
            try:
                info = _call(settings, "GET", "/api/config")
                return {"ok": True, "message": f"Connected to {info.get('location_name', 'Home Assistant')} "
                                               f"(version {info.get('version', '?')})"}
            except HAError as e:
                return {"ok": False, "message": str(e)}
        if name == "entities":
            domains = payload.get("domains") if isinstance(payload, dict) else None
            out = []
            for s in _call(settings, "GET", "/api/states") or []:
                eid = s.get("entity_id", "")
                if domains and eid.split(".")[0] not in domains:
                    continue
                friendly = (s.get("attributes") or {}).get("friendly_name") or eid
                out.append({"value": eid, "label": eid if friendly == eid else f"{friendly} ({eid})"})
            return sorted(out, key=lambda o: o["label"].lower())
        return super().api(name, payload, ctx)


    def widget(self, name, params, ctx):
        if name == "alert":
            return self._alert(params, ctx)
        if name != "state":
            return super().widget(name, params, ctx)
        ent = self._need(params.get("entity"))
        st = None
        if (getattr(ctx, "app", None) is not None and getattr(ctx.app, "live", None) is not None
                and (ctx.settings.get("token") or "").strip()):
            # instant updates: a WebSocket to HA keeps every state; REST is the fallback while it's down
            st = _ha_live.link(_base(ctx.settings), (ctx.settings.get("token") or "").strip(), ctx.app).get(ent)
        if st is None:
            st = _call(ctx.settings, "GET", "/api/states/" + urllib.parse.quote(ent, safe=".")) or {}
        state = str(st.get("state", "?"))
        attrs = st.get("attributes") or {}
        domain = ent.split(".")[0]
        if domain in TOGGLE_DOMAINS or domain in ("binary_sensor", "person", "device_tracker"):
            on = state in ON_STATES
            out = {"on": on}
            rgb = attrs.get("rgb_color")
            if on and isinstance(rgb, list) and len(rgb) == 3:
                out["color"] = [int(c) for c in rgb]
            if domain == "light" and on and attrs.get("brightness") is not None:
                out["progress"] = round(int(attrs["brightness"]) / 255, 2)
            return out
        unit = attrs.get("unit_of_measurement") or ""
        try:
            num = float(state)
            state = (f"{num:.1f}" if abs(num) < 100 and num != int(num) else f"{num:.0f}")
        except ValueError:
            pass
        return {"text": (state + ("°" if unit in ("°C", "°F") else (" " + unit if unit and len(unit) <= 3 else ""))).strip()}


    def _alert(self, params, ctx):
        """Lit from the moment the entity turns to the chosen state until a Clear key is pressed."""
        ent = self._need(params.get("entity"))
        want = (params.get("state") or "on").strip()
        name = (params.get("name") or "").strip() or ent.split(".", 1)[-1].replace("_", " ").title()
        when = None
        link = None
        if (getattr(ctx, "app", None) is not None and getattr(ctx.app, "live", None) is not None
                and (ctx.settings.get("token") or "").strip()):
            link = _ha_live.link(_base(ctx.settings), ctx.settings["token"].strip(), ctx.app)
        st = link.get(ent) if link else None
        if link is not None and st is not None:
            when = link.entered.get((ent, want))
        if st is None:
            st = _call(ctx.settings, "GET", "/api/states/" + urllib.parse.quote(ent, safe=".")) or {}
        if st.get("state") == want:   # it's in that state now: count from when it changed
            when = max(when or 0, _iso_time(st.get("last_changed")) or 0) or time.time()
        if when is None or when <= _ALERT_CLEARED.get(ent, _STARTED):
            return {"icon": "🔔", "text": "", "label": name[:14], "on": False}
        ago = max(0, int(time.time() - when))
        text = "now" if ago < 60 else f"{ago // 60}m ago" if ago < 3600 else f"{ago // 3600}h ago"
        return {"icon": "🔔", "text": text, "label": name[:14], "on": True, "color": [255, 80, 80]}


_ALERT_CLEARED = {}       # entity -> time the alert key was cleared
_STARTED = time.time()    # alerts from before VSD Pad started don't count


def _iso_time(v):
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


ON_STATES = {"on", "open", "opening", "playing", "home", "unlocked", "heat", "cool", "auto", "heat_cool"}

PLUGIN = HomeAssistant()
