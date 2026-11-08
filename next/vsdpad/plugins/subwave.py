"""SUB/WAVE (github.com/perminder-klair/subwave): control your AI radio station from the pad.

Talks to the station's controller API: <station>/api (Caddy, :7700) or the controller directly (:7701).
Public calls (now playing, like, request) need nothing, or the station password if the station has one.
On-air controls (skip, switch show, jingles, DJ) use the admin login (ADMIN_USER / ADMIN_PASS) over HTTP Basic.
Logins are sent only to the station address saved in the settings.
"""
import base64, json, re, threading, time, urllib.error, urllib.parse, urllib.request

from .base import Plugin

TIMEOUT = 6
CACHE_S = 1.5   # several live keys share one now-playing / schedule fetch

FOR_OPTIONS = [{"value": "schedule-change", "label": "Until the next scheduled show"},
               {"value": "30", "label": "For 30 minutes"}, {"value": "60", "label": "For 1 hour"},
               {"value": "120", "label": "For 2 hours"}, {"value": "240", "label": "For 4 hours"}]
SHOW = {"key": "show", "label": "Show", "kind": "choice", "api": "shows"}
POSITION = {"key": "position", "label": "Which song", "kind": "select",
            "options": [{"value": str(i), "label": ["Next", "2nd", "3rd", "4th", "5th"][i - 1]} for i in range(1, 6)]}


class SubWaveError(Exception):
    pass


def _api_base(settings):
    url = (settings.get("url") or "").strip().rstrip("/")
    if not url:
        raise SubWaveError("Set your station address in Plugins → SUB/WAVE.")
    if "://" not in url:
        url = "http://" + url
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise SubWaveError("The station address must start with http:// or https://")
    # the controller itself (:7701) or an address already ending in /api is used as is
    if u.port == 7701 or u.path.rstrip("/").endswith("/api"):
        return url
    return url + "/api"


def _has_admin(settings):
    return bool((settings.get("username") or "").strip() and settings.get("password"))


def _call(settings, method, path, body=None, admin=False):
    base = _api_base(settings)
    headers = {"Accept": "application/json", "User-Agent": "vsdpad"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if settings.get("station_password"):
        headers["x-station-auth"] = settings["station_password"]
    if admin:
        if not _has_admin(settings):
            raise SubWaveError("Add your SUB/WAVE admin user and password in Plugins → SUB/WAVE to use this key.")
        cred = f"{settings['username'].strip()}:{settings['password']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(cred).decode()
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise SubWaveError("SUB/WAVE didn't accept the admin login." if admin
                               else "SUB/WAVE needs the station password (Plugins → SUB/WAVE).") from None
        msg = ""
        try:
            msg = json.loads(e.read() or b"{}").get("error") or ""
        except (ValueError, AttributeError, OSError):
            pass
        raise SubWaveError(f"SUB/WAVE: {msg}" if msg else f"SUB/WAVE error {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SubWaveError(f"Can't reach your station at {base} ({getattr(e, 'reason', e)})") from None
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("error"):
        raise SubWaveError(f"SUB/WAVE: {data['error']}")
    return data


_cache, _cache_lock = {}, threading.Lock()


def _cached(settings, path):
    key = (_api_base(settings), settings.get("station_password") or "", path)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    data = _call(settings, "GET", path) or {}
    with _cache_lock:
        _cache[key] = (now, data)
    return data


def _forget():
    with _cache_lock:
        _cache.clear()


# ---- reading SUB/WAVE's loosely-shaped answers ----
def _txt(v):
    if v is None or v is False:
        return ""
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, list):
        return ", ".join(x for x in map(_txt, v) if x)
    if isinstance(v, dict):
        for k in ("name", "title", "label", "text", "value"):
            if v.get(k) is not None:
                return _txt(v[k])
    return ""


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _track(raw):
    r = raw.get("track", raw) if isinstance(raw, dict) else {}
    r = r if isinstance(r, dict) else {}
    dur = _num(r.get("duration"))
    if dur and dur > 36000:     # milliseconds
        dur /= 1000
    started = _num(r.get("startedAt"))
    if started and started < 1e11:   # seconds -> ms
        started *= 1000
    return {"title": re.sub(r"^\d{1,3}\s*[-.]\s+", "", _txt(r.get("title") or r.get("name"))),
            "artist": _txt(r.get("artist") or r.get("artists")), "album": _txt(r.get("album")),
            "song_id": _txt(r.get("subsonic_id") or r.get("subsonicId") or r.get("id")),
            "duration": dur, "elapsed": _num(r.get("elapsed")), "startedAt": started}


def _active_show(np):
    s = np.get("activeShow") or (np.get("context") or {}).get("activeShow")
    if isinstance(s, dict):
        return {"id": _txt(s.get("id")), "name": _txt(s.get("name"))}
    return {"id": "", "name": _txt(s)}


def _listeners(np):
    v = np.get("listeners", (np.get("context") or {}).get("listeners"))
    if isinstance(v, dict):
        v = v.get("current", v.get("count"))
    n = _num(v)
    return int(n) if n is not None else 0


def _short(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


_peak = {}


def _pos(v):
    try:
        return max(1, min(5, int(v or 1)))
    except (TypeError, ValueError):
        return 1


def _upcoming(settings, position):
    """The Nth song in Up next (1 = the next one), or None."""
    items = (_cached(settings, "/state") or {}).get("upcoming") or []
    i = _pos(position) - 1
    return _track(items[i]) if i < len(items) and isinstance(items[i], dict) else None


def _shows(settings):
    sched = _cached(settings, "/schedule")
    out = []
    for s in sched.get("shows") or []:
        if isinstance(s, dict) and s.get("id") is not None:
            out.append({"id": str(s["id"]), "name": _txt(s.get("name")) or str(s["id"])})
    return out, sched.get("override")


class SubWave(Plugin):
    id = "subwave"
    name = "SUB/WAVE"
    description = "Skip songs, switch shows, play jingles and see what's on air on your SUB/WAVE radio station."
    settings = [
        {"key": "url", "label": "Station address", "placeholder": "http://192.168.1.20:7700",
         "help": "The address you open SUB/WAVE at (port 7700 by default)."},
        {"key": "username", "label": "Admin user", "placeholder": "admin",
         "help": "Your SUB/WAVE admin login (ADMIN_USER). Needed for skip, switch show, jingles and the DJ keys."},
        {"key": "password", "label": "Admin password", "secret": True},
        {"key": "station_password", "label": "Station password (only if your station has one)", "secret": True},
    ]
    buttons = [{"id": "test", "label": "Test connection", "api": "test"}]
    actions = {
        "skip": {"label": "Skip song", "params": []},
        "show": {"label": "Switch show now", "params": [
            {**SHOW, "help": "“Default programming” plays the station's normal music with no show."},
            {"key": "for", "label": "How long", "kind": "select", "options": FOR_OPTIONS}]},
        "schedule": {"label": "Back to the schedule", "params": []},
        "like": {"label": "Like this song", "params": []},
        "jingle": {"label": "Play a jingle", "params": [{"key": "jingle", "label": "Jingle", "kind": "choice", "api": "jingles"}]},
        "sfx": {"label": "Play a sound effect", "params": [{"key": "sfx", "label": "Sound effect", "kind": "choice", "api": "sfx"}]},
        "say": {"label": "DJ says…", "params": [
            {"key": "text", "label": "What to say", "kind": "text", "placeholder": "Shout-out to everyone listening!"},
            {"key": "mode", "label": "How", "kind": "select", "options": [
                {"value": "styled", "label": "In the DJ's own words"}, {"value": "raw", "label": "Word for word"}]}]},
        "skill": {"label": "DJ segment", "params": [{"key": "skill", "label": "Segment", "kind": "choice", "api": "skills",
                                                     "help": "Things your DJ can do, like weather or news."}]},
        "refresh": {"label": "Refresh playlist", "params": []},
        "remove_next": {"label": "Remove a song from Up next", "params": [POSITION]},
        "request": {"label": "Request a song", "params": [
            {"key": "text", "label": "Request", "kind": "text", "placeholder": "something upbeat from the 80s",
             "help": "Anything you'd type into the station's request box."}]},
    }
    widgets = {
        "now": {"label": "On air", "interval": 3, "params": [],
                "sample": {"icon": "📻", "text": "Midnight C…", "label": "The Artist", "progress": 0.4}},
        "show": {"label": "Current show", "interval": 10, "params": [
            {**SHOW, "api": "shows_live", "label": "Light up for show",
             "help": "Pick a show to make this key light up only while that show is on air (good on a “Switch show” key)."}],
                 "sample": {"icon": "🎙", "text": "Chill", "label": "On air"}},
        "listeners": {"label": "Listeners", "interval": 15, "params": [], "sample": {"text": "3", "label": "Listening"}},
        "liked": {"label": "Liked?", "interval": 5, "params": [], "sample": {"icon": "♥", "label": "Liked", "on": True}},
        "next": {"label": "Up next", "interval": 10, "params": [POSITION],
                 "sample": {"icon": "⏭", "text": "Song Title", "label": "Up next"}},
        "peak": {"label": "Peak listeners today", "interval": 30, "params": [], "sample": {"text": "12", "label": "Peak today"}},
        "requests": {"label": "Requests played", "interval": 30, "params": [], "sample": {"text": "8/9", "label": "Requests"}},
    }

    def run(self, action, params, ctx):
        s = ctx.settings
        if action == "skip":
            _call(s, "POST", "/dj/skip", {}, admin=True)
        elif action == "show":
            until = params.get("for") or "schedule-change"
            body = {"showId": params.get("show") or None, "until": "schedule-change" if until == "schedule-change" else "fixed"}
            if body["until"] == "fixed":
                body["minutes"] = int(float(until))
            _call(s, "POST", "/schedule/override", body, admin=True)
        elif action == "schedule":
            _call(s, "DELETE", "/schedule/override", admin=True)
        elif action == "like":
            _call(s, "POST", "/like", {})
        elif action == "jingle":
            if not params.get("jingle"):
                raise SubWaveError("Pick a jingle for this key.")
            _call(s, "POST", f"/jingles/{urllib.parse.quote(params['jingle'], safe='')}/play", {}, admin=True)
        elif action == "sfx":
            if not params.get("sfx"):
                raise SubWaveError("Pick a sound effect for this key.")
            _call(s, "POST", f"/sfx/{urllib.parse.quote(params['sfx'], safe='')}/play", {}, admin=True)
        elif action == "say":
            text = (params.get("text") or "").strip()
            if not text:
                raise SubWaveError("Type what the DJ should say on this key.")
            _call(s, "POST", "/dj/say", {"text": text, "mode": "raw" if params.get("mode") == "raw" else "styled"}, admin=True)
        elif action == "skill":
            if not params.get("skill"):
                raise SubWaveError("Pick a DJ segment for this key.")
            _call(s, "POST", "/dj/skill", {"name": params["skill"]}, admin=True)
        elif action == "refresh":
            _call(s, "POST", "/dj/refresh-playlist", {}, admin=True)
        elif action == "remove_next":
            t = _upcoming(s, params.get("position"))
            if t is None:
                raise SubWaveError("There's no song there in Up next.")
            if not t["song_id"]:
                raise SubWaveError("SUB/WAVE didn't say which song that is, so it can't be removed.")
            _call(s, "DELETE", f"/dj/queue/{urllib.parse.quote(t['song_id'], safe='')}", admin=True)
        elif action == "request":
            text = (params.get("text") or "").strip()
            if not text:
                raise SubWaveError("Type the song request on this key.")
            _call(s, "POST", "/request", {"text": text, "name": "VSD Pad"})
        else:
            raise ValueError(f"unknown SUB/WAVE action {action!r}")
        _forget()   # live keys show the change straight away

    def widget(self, name, params, ctx):
        s = ctx.settings
        if name == "now":
            np = _cached(s, "/now-playing")
            t = _track(np.get("nowPlaying") or {})
            if not t["title"]:
                return {"icon": "📻", "text": "Off air", "on": False}
            out = {"icon": "📻", "text": _short(t["title"], 11), "label": _short(t["artist"] or t["album"], 14)}
            if t["duration"]:
                el = (time.time() * 1000 - t["startedAt"]) / 1000 if t["startedAt"] else t["elapsed"]
                if el is not None:
                    out["progress"] = round(max(0.0, min(1.0, el / t["duration"])), 3)
            return out
        if name == "show":
            np = _cached(s, "/now-playing")
            cur = _active_show(np)
            want = params.get("show") or ""
            if want:
                try:
                    shows, _ = _shows(s)
                except SubWaveError:
                    shows = []
                wname = next((x["name"] for x in shows if x["id"] == want), want)
                on = want in (cur["id"], cur["name"]) or wname == cur["name"]
                return {"icon": "🎙", "text": _short(wname, 11), "label": "On air" if on else "", "on": on}
            if not cur["name"]:
                return {"icon": "🎙", "text": "Music", "label": "No show", "on": False}
            return {"icon": "🎙", "text": _short(cur["name"], 11), "label": "On air"}
        if name == "listeners":
            n = _listeners(_cached(s, "/now-playing"))
            return {"text": str(n), "label": "Listening", "on": n > 0}
        if name == "next":
            pos = _pos(params.get("position"))
            t = _upcoming(s, pos)
            if t is None:
                return {"icon": "⏭", "text": "—", "label": "Queue empty" if pos == 1 else f"#{pos}", "on": False}
            return {"icon": "⏭", "text": _short(t["title"], 11), "label": _short(t["artist"], 14) or ("Up next" if pos == 1 else f"#{pos}")}
        if name == "peak":
            n = _listeners(_cached(s, "/now-playing"))
            day = time.strftime("%Y-%m-%d")
            with _cache_lock:
                if _peak.get("day") != day:
                    _peak.update(day=day, n=0)
                _peak["n"] = max(_peak["n"], n)
                peak = _peak["n"]
            return {"text": str(peak), "label": "Peak today", "on": peak > 0}
        if name == "requests":
            st = (_call(s, "GET", "/stats", admin=True) or {}).get("requests") or {}
            total, done = int(_num(st.get("total")) or 0), int(_num(st.get("resolved")) or 0)
            return {"text": f"{done}/{total}", "label": "Requests", "on": total > 0}
        if name == "liked":
            st = _cached(s, "/like")
            liked = bool(st.get("liked"))
            return {"icon": "♥" if liked else "♡", "label": "Liked" if liked else "Like", "on": liked}
        return super().widget(name, params, ctx)

    def api(self, name, payload, ctx):
        s = ctx.settings
        if name == "test":
            try:
                np = _call(s, "GET", "/now-playing") or {}
                station = _txt((np.get("dj") or {}).get("station")) if isinstance(np.get("dj"), dict) else ""
                msg = f"Connected to {station or 'SUB/WAVE'}, {_listeners(np)} listening."
                if _has_admin(s):
                    _call(s, "GET", "/settings", admin=True)
                    msg += " Admin login OK."
                else:
                    msg += " Add the admin login to use skip, switch show and the DJ keys."
                return {"ok": True, "message": msg}
            except SubWaveError as e:
                return {"ok": False, "message": str(e)}
        try:
            if name in ("shows", "shows_live"):
                shows, _ = _shows(s)
                first = "Default programming" if name == "shows" else "Show whatever is on air"
                return [{"value": "", "label": first}] + [{"value": x["id"], "label": x["name"]} for x in shows]
            if name == "jingles":
                items = (_call(s, "GET", "/jingles", admin=True) or {}).get("jingles") or []
                return [{"value": j["filename"], "label": _txt(j.get("text")) or j["filename"]}
                        for j in items if isinstance(j, dict) and j.get("filename")]
            if name == "sfx":
                items = (_call(s, "GET", "/sfx", admin=True) or {}).get("sfx") or []
                return [{"value": x["name"], "label": x["name"] + (f" ({x['description']})" if x.get("description") else "")}
                        for x in items if isinstance(x, dict) and x.get("name")]
            if name == "skills":
                items = (_call(s, "GET", "/dj/skills", admin=True) or {}).get("skills") or []
                return [{"value": x["kind"], "label": _txt(x.get("label")) or x["kind"]}
                        for x in items if isinstance(x, dict) and x.get("kind")]
        except SubWaveError:
            if name in ("shows", "shows_live"):
                return [{"value": "", "label": "Default programming" if name == "shows" else "Show whatever is on air"}]
            return []
        return super().api(name, payload, ctx)


PLUGIN = SubWave()
