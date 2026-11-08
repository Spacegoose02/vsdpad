"""Stats keys: live numbers on a key - Steam playtime, Twitch viewers, or any number from a web address.

Steam: a free Web API key (steamcommunity.com/dev/apikey) and your profile must show game details publicly.
Twitch: a free app at dev.twitch.tv/console/apps gives a Client ID and Client Secret.
Keys are sent only to Steam's and Twitch's own servers.
"""
import json, re, threading, time, urllib.error, urllib.parse, urllib.request

from .base import Plugin

TIMEOUT = 8
_lock = threading.Lock()
_cache = {}
_token = {}


class StatsError(Exception):
    pass


def _get(url, headers=None, data=None, what="that site"):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "vsdpad", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise StatsError(f"{what} didn't accept the key.") from None
        raise StatsError(f"{what} error {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise StatsError(f"Can't reach {what} ({getattr(e, 'reason', e)})") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise StatsError(f"{what} didn't send back data VSD Pad can read.") from None


def _cached(key, seconds, fn):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < seconds:
            return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (now, v)
    return v


def _num(n):
    n = float(n)
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 1e4:
        return f"{n / 1e3:.0f}k"
    if abs(n) >= 1000:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}" if n == int(n) else f"{n:.1f}"


# ---------------------------------------------------------------- Steam
STEAM = "https://api.steampowered.com"
TWITCH_ID = "https://id.twitch.tv/oauth2/token"
TWITCH_API = "https://api.twitch.tv/helix/"


def _steam_key(s):
    key = (s.get("steam_key") or "").strip()
    if not key:
        raise StatsError("Add your Steam Web API key in Plugins → Stats (steamcommunity.com/dev/apikey).")
    return key


def _steam_id(s):
    raw = (s.get("steam_id") or "").strip().rstrip("/")
    if not raw:
        raise StatsError("Add your Steam ID or profile link in Plugins → Stats.")
    m = re.search(r"(\d{17})", raw)
    if m:
        return m.group(1)
    name = raw.split("/")[-1]

    def resolve():
        r = _get(f"{STEAM}/ISteamUser/ResolveVanityURL/v1/?" + urllib.parse.urlencode({"key": _steam_key(s), "vanityurl": name}),
                 what="Steam").get("response") or {}
        if r.get("success") != 1:
            raise StatsError(f"Steam doesn't know a profile called {name}.")
        return r["steamid"]
    return _cached(("vanity", name), 86400, resolve)


def _owned(s):
    def load():
        r = _get(f"{STEAM}/IPlayerService/GetOwnedGames/v1/?" + urllib.parse.urlencode(
            {"key": _steam_key(s), "steamid": _steam_id(s), "include_appinfo": 1, "include_played_free_games": 1}),
            what="Steam").get("response") or {}
        if "games" not in r:
            raise StatsError("Steam isn't sharing your games. Set Game details to Public in your Steam privacy settings.")
        return r["games"]
    return _cached(("owned", _steam_id(s)), 300, load)


def _hours(minutes):
    h = (minutes or 0) / 60
    return f"{h:.0f}h" if h >= 10 else f"{h:.1f}h"


# ---------------------------------------------------------------- Twitch
def _twitch_token(s, force=False):
    cid, secret = (s.get("twitch_id") or "").strip(), (s.get("twitch_secret") or "").strip()
    if not (cid and secret):
        raise StatsError("Add a Twitch Client ID and Secret in Plugins → Stats (dev.twitch.tv/console/apps).")
    with _lock:
        t = _token.get(cid)
        if t and not force and t[1] > time.time() + 60:
            return cid, t[0]
    r = _get(TWITCH_ID, data=urllib.parse.urlencode(
        {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).encode(), what="Twitch")
    with _lock:
        _token[cid] = (r["access_token"], time.time() + float(r.get("expires_in", 3600)))
    return cid, r["access_token"]


def _helix(s, path, params):
    for attempt in (0, 1):
        cid, tok = _twitch_token(s, force=attempt == 1)
        try:
            return _get(TWITCH_API + path + "?" + urllib.parse.urlencode(params),
                        headers={"Client-Id": cid, "Authorization": "Bearer " + tok}, what="Twitch")
        except StatsError as e:
            if attempt == 0 and "didn't accept" in str(e):
                continue
            raise


# ---------------------------------------------------------------- any web number
def pick(data, path):
    """data.items.0.count style path into JSON."""
    cur = data
    for part in [p for p in re.split(r"[.\[\]]", path or "") if p]:
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise StatsError(f"No item {part} in the list.") from None
        elif isinstance(cur, dict):
            if part not in cur:
                raise StatsError(f"No “{part}” in the data.")
            cur = cur[part]
        else:
            raise StatsError(f"Can't go into “{part}”.")
    return cur


class Stats(Plugin):
    id = "stats"
    name = "Stats"
    description = "Live numbers on keys: Steam playtime, Twitch viewers, or any number from a web address."
    settings = [
        {"key": "steam_key", "label": "Steam Web API key", "secret": True,
         "help": "Free at steamcommunity.com/dev/apikey (any domain name works)."},
        {"key": "steam_id", "label": "Steam profile", "placeholder": "https://steamcommunity.com/id/yourname",
         "help": "Your profile link or 17-digit Steam ID."},
        {"key": "twitch_id", "label": "Twitch Client ID", "help": "Make a free app at dev.twitch.tv/console/apps."},
        {"key": "twitch_secret", "label": "Twitch Client Secret", "secret": True},
    ]
    buttons = [{"id": "test", "label": "Test", "api": "test"}]
    actions = {}
    widgets = {
        "steam_game": {"label": "Steam: hours in a game", "interval": 300, "params": [
            {"key": "appid", "label": "Game", "kind": "choice", "api": "games"}],
                       "sample": {"text": "312h", "label": "Rust"}},
        "steam_total": {"label": "Steam: hours in the last 2 weeks", "interval": 600, "params": [],
                        "sample": {"text": "21h", "label": "2 weeks"}},
        "steam_now": {"label": "Steam: playing now", "interval": 60, "params": [],
                      "sample": {"icon": "🎮", "text": "Rust", "label": "Playing", "on": True}},
        "twitch": {"label": "Twitch: viewers", "interval": 60, "params": [
            {"key": "channel", "label": "Channel", "kind": "text", "placeholder": "yourchannel"}],
                   "sample": {"icon": "📺", "text": "128", "label": "LIVE", "on": True}},
        "web": {"label": "Any number from a web address", "interval": 300, "params": [
            {"key": "url", "label": "Address (JSON)", "kind": "text", "placeholder": "https://api.example.com/stats"},
            {"key": "path", "label": "Where the number is", "kind": "text", "placeholder": "data.followers",
             "help": "Dots between names, numbers for list items, e.g. items.0.count. Leave empty if the address returns just a number."},
            {"key": "label", "label": "Name under the number", "kind": "text", "placeholder": "Followers"},
            {"key": "every", "label": "Check every", "kind": "select", "options": [
                {"value": "60", "label": "minute"}, {"value": "300", "label": "5 minutes"}, {"value": "3600", "label": "hour"}]}],
                "sample": {"text": "1.2k", "label": "Followers"}},
    }

    def widget(self, name, params, ctx):
        s = ctx.settings
        if name == "steam_game":
            appid = str(params.get("appid") or "")
            if not appid:
                return {"text": "?", "label": "Pick a game", "on": False}
            g = next((g for g in _owned(s) if str(g.get("appid")) == appid), None)
            if g is None:
                raise StatsError("That game isn't in your Steam library.")
            return {"text": _hours(g.get("playtime_forever")), "label": (g.get("name") or "Game")[:14]}
        if name == "steam_total":
            r = _cached(("recent", _steam_id(s)), 600, lambda: _get(f"{STEAM}/IPlayerService/GetRecentlyPlayedGames/v1/?" + urllib.parse.urlencode(
                {"key": _steam_key(s), "steamid": _steam_id(s)}), what="Steam").get("response") or {})
            mins = sum(g.get("playtime_2weeks", 0) for g in r.get("games") or [])
            return {"text": _hours(mins), "label": "2 weeks"}
        if name == "steam_now":
            r = _get(f"{STEAM}/ISteamUser/GetPlayerSummaries/v2/?" + urllib.parse.urlencode(
                {"key": _steam_key(s), "steamids": _steam_id(s)}), what="Steam").get("response", {}).get("players") or [{}]
            game = r[0].get("gameextrainfo")
            if not game:
                return {"icon": "🎮", "text": "—", "label": "Not playing", "on": False}
            return {"icon": "🎮", "text": game[:11], "label": "Playing", "on": True}
        if name == "twitch":
            ch = (params.get("channel") or "").strip().lower().lstrip("@").split("/")[-1]
            if not ch:
                return {"icon": "📺", "text": "?", "label": "Pick channel", "on": False}
            live = (_helix(s, "streams", {"user_login": ch}).get("data") or [])
            if not live:
                return {"icon": "📺", "text": "Offline", "label": ch[:14], "on": False}
            return {"icon": "📺", "text": _num(live[0].get("viewer_count", 0)), "label": "LIVE", "on": True, "color": [180, 120, 255]}
        if name == "web":
            url = (params.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                return {"text": "?", "label": "Add address", "on": False}
            every = float(params.get("every") or 300)
            data = _cached(("web", url), every - 1, lambda: _get(url, what=urllib.parse.urlparse(url).hostname or "that site"))
            v = pick(data, params.get("path"))
            try:
                text = _num(float(v))
            except (TypeError, ValueError):
                text = str(v)[:11]
            return {"text": text, "label": (params.get("label") or "")[:14]}
        return super().widget(name, params, ctx)

    def api(self, name, payload, ctx):
        s = ctx.settings
        if name == "test":
            parts, ok = [], True
            if s.get("steam_key") or s.get("steam_id"):
                try:
                    parts.append(f"Steam works: {len(_owned(s))} games.")
                except StatsError as e:
                    ok = False
                    parts.append(str(e))
            if s.get("twitch_id") or s.get("twitch_secret"):
                try:
                    _twitch_token(s, force=True)
                    parts.append("Twitch works.")
                except StatsError as e:
                    ok = False
                    parts.append(str(e))
            return {"ok": ok, "message": " ".join(parts) or "Add a Steam or Twitch key first. The web-address key needs no setup."}
        if name == "games":
            try:
                games = sorted(_owned(s), key=lambda g: -(g.get("playtime_forever") or 0))
            except StatsError:
                return []
            return [{"value": str(g["appid"]), "label": f"{g.get('name', g['appid'])} ({_hours(g.get('playtime_forever'))})"}
                    for g in games[:300]]
        return super().api(name, payload, ctx)


PLUGIN = Stats()
