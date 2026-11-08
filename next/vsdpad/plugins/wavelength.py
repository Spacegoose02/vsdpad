"""Wavelength (github.com/Spacegoose02/wavelength): control and show your Wavelength players through your Hub.

Uses the Hub's Home Assistant API, so it works for every device you have Wavelength open on (this PC, a phone,
Android TV). Make a token in Wavelength: Settings → Account → Home Assistant → Make a Home Assistant token.
The token is sent only to the Hub address saved in the settings.
"""
import json, urllib.error, urllib.parse, urllib.request

from .base import Plugin

TIMEOUT = 6
PLAYER = {"key": "player", "label": "Player", "kind": "choice", "api": "players",
          "help": "Leave on “Whichever is playing” to control the device that's playing right now."}


class WavelengthError(Exception):
    pass


def _base(settings):
    url = (settings.get("url") or "").strip().rstrip("/")
    if not url:
        raise WavelengthError("Set your Hub address in Plugins → Wavelength.")
    if "://" not in url:
        url = "https://" + url
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise WavelengthError("The Hub address must start with http:// or https://")
    return url


def _call(settings, method, path, body=None):
    base = _base(settings)
    token = (settings.get("token") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise WavelengthError("Add your Wavelength token in Plugins → Wavelength (Wavelength: Settings → Account → Home Assistant).")
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                          "User-Agent": "vsdpad"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}")
        except (ValueError, OSError):
            detail = {}
        msg = str(detail.get("error") or detail.get("message") or "")
        if e.code == 404 and msg.startswith("Route "):
            raise WavelengthError("Your Hub is missing this feature. Update Wavelength Hub to the latest version.") from None
        if e.code == 404 and msg:
            raise WavelengthError(msg) from None
        if e.code in (400, 409) and msg:
            raise WavelengthError(msg) from None
        if e.code in (401, 403):
            raise WavelengthError("The Hub didn't accept the token. Make a new one in Wavelength.") from None
        if e.code == 404:
            raise WavelengthError("That player isn't open right now.") from None
        raise WavelengthError(f"Hub error {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WavelengthError(f"Can't reach your Hub at {base} ({getattr(e, 'reason', e)})") from None
    return json.loads(raw) if raw else None


def _players(settings):
    return (_call(settings, "GET", "/api/ha/players") or {}).get("players") or []


def _pick(players, name):
    """The player named `name` (any case), or with no name the one that's playing (else paused, else any)."""
    name = (name or "").strip().lower()
    if name:
        return next((p for p in players if str(p.get("name", "")).lower() == name), None)
    for want in ("playing", "paused"):
        p = next((p for p in players if p.get("state") == want), None)
        if p:
            return p
    return players[0] if players else None


def _short(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


class Wavelength(Plugin):
    id = "wavelength"
    name = "Wavelength"
    description = "Play, pause, skip and see what's playing in Wavelength, on this PC or any of your devices."
    settings = [
        {"key": "url", "label": "Hub address", "placeholder": "https://hub.example.com"},
        {"key": "token", "label": "Home Assistant token", "secret": True,
         "help": "In Wavelength: Settings → Account → Home Assistant → Make a Home Assistant token. Paste it here."},
    ]
    buttons = [{"id": "test", "label": "Test connection", "api": "test"}]
    actions = {
        "toggle": {"label": "Play / pause", "params": [PLAYER]},
        "play": {"label": "Play", "params": [PLAYER]},
        "pause": {"label": "Pause", "params": [PLAYER]},
        "next": {"label": "Next song", "params": [PLAYER]},
        "prev": {"label": "Previous song", "params": [PLAYER]},
        "volume": {"label": "Volume up / down", "params": [PLAYER, {"key": "step", "label": "Change (%)", "kind": "number",
                                                                    "default": 10, "help": "e.g. 10 or -10"}]},
        "volume_set": {"label": "Set volume", "params": [PLAYER, {"key": "value", "label": "Volume (%)", "kind": "number", "default": 50}]},
        "handoff": {"label": "Move playback to another device", "params": [
            {**PLAYER, "label": "From"},
            {"key": "to", "label": "To", "kind": "choice", "api": "targets",
             "help": "The device picks up the same song at the same spot, and the one it came from pauses."}]},
        "seek": {"label": "Skip forward / back", "params": [PLAYER, {"key": "step", "label": "Seconds", "kind": "number",
                                                                     "default": 15, "help": "e.g. 15 or -15"}]},
    }
    widgets = {
        "now": {"label": "Now playing", "interval": 2, "params": [PLAYER],
                "sample": {"icon": "▶", "text": "Midnight C…", "label": "The Artist", "progress": 0.4}},
        "volume": {"label": "Volume", "interval": 3, "params": [PLAYER], "sample": {"text": "70%", "label": "Volume", "progress": 0.7}},
    }

    def _target(self, settings, params):
        players = _players(settings)
        p = _pick(players, params.get("player"))
        if p is None:
            raise WavelengthError("No Wavelength player is open." if not params.get("player")
                                  else f"{params['player']} isn't open in Wavelength right now.")
        return p

    def run(self, action, params, ctx):
        if action not in self.actions:
            raise ValueError(f"unknown Wavelength action {action!r}")
        p = self._target(ctx.settings, params)
        if action == "handoff":
            to = (params.get("to") or "").strip()
            if not to:
                raise WavelengthError("Pick the device to move the music to.")
            if to.lower() == str(p.get("name", "")).lower():
                raise WavelengthError(f"{p.get('name')} is already the one playing.")
            _call(ctx.settings, "POST", f"/api/ha/players/{int(p['id'])}/handoff", {"to": to})
            return
        body = {"action": action}
        if action == "volume":
            body = {"action": "volume", "value": max(0.0, min(1.0, float(p.get("volume", 1)) + float(params.get("step", 10)) / 100))}
        elif action == "volume_set":
            body = {"action": "volume", "value": max(0.0, min(1.0, float(params.get("value", 50)) / 100))}
        elif action == "seek":
            body = {"action": "seek", "value": max(0, float(p.get("position", 0)) + float(params.get("step", 15)))}
        _call(ctx.settings, "POST", f"/api/ha/players/{int(p['id'])}", body)

    def widget(self, name, params, ctx):
        if name not in self.widgets:
            return super().widget(name, params, ctx)
        p = _pick(_players(ctx.settings), params.get("player"))
        if p is None:
            return {"icon": "♪", "text": "Off", "on": False}
        if name == "volume":
            v = float(p.get("volume", 1))
            return {"text": f"{round(v * 100)}%", "label": "Volume", "progress": round(v, 3)}
        if p.get("state") == "idle" or not p.get("title"):
            return {"icon": "♪", "text": "—", "label": _short(p.get("name"), 14), "on": False}
        dur = float(p.get("duration") or 0)
        out = {"icon": "▶" if p.get("state") == "playing" else "⏸", "text": _short(p.get("title"), 11),
               "label": _short(p.get("artist") or p.get("album") or p.get("name"), 14), "on": p.get("state") == "playing"}
        if dur > 0:
            out["progress"] = round(min(1.0, float(p.get("position", 0)) / dur), 3)
        return out

    def api(self, name, payload, ctx):
        if name == "test":
            try:
                players = _players(ctx.settings)
                names = ", ".join(p.get("name", "?") for p in players)
                return {"ok": True, "message": f"Connected. Open players: {names}" if players
                        else "Connected. No Wavelength player is open right now."}
            except WavelengthError as e:
                return {"ok": False, "message": str(e)}
        if name == "targets":
            try:
                return [{"value": p.get("name", ""), "label": f"{p.get('name')} ({p.get('platform', 'device')})"}
                        for p in _players(ctx.settings)]
            except WavelengthError:
                return []
        if name == "players":
            out = [{"value": "", "label": "Whichever is playing"}]
            try:
                players = _players(ctx.settings)
            except WavelengthError:
                players = []
            for p in players:
                out.append({"value": p.get("name", ""), "label": f"{p.get('name')} ({p.get('platform', 'device')})"})
            return out
        return super().api(name, payload, ctx)


PLUGIN = Wavelength()
