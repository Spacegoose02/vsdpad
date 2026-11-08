"""Now playing (Windows): see and control whatever is playing on this PC - Spotify, a browser tab, VLC, Wavelength,
Apple Music... anything that shows in the Windows volume / media pop-up.

Uses the Windows media session API through the small "winrt" packages:
    pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections
"""
import asyncio, re, sys, threading, time

from .base import Plugin

APP = {"key": "app", "label": "App", "kind": "choice", "api": "apps",
       "help": "Leave on “Whatever Windows is showing” to use the player Windows picks (the one in the media pop-up)."}
CACHE_S = 0.8
INSTALL = "Now playing needs a small add-on: run  pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections"

_lock = threading.Lock()
_cache = {}


def _mc():
    if sys.platform != "win32":
        raise OSError("Now playing only works on Windows.")
    try:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
    except ImportError:
        raise OSError(INSTALL) from None
    return M


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _app_name(aumid):
    """'Spotify.exe' / 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify' / 'Chrome' -> a readable name."""
    s = (aumid or "").split("!")[-1]
    s = re.sub(r"\.exe$", "", s, flags=re.I)
    s = s.split(".")[-1] if "." in s and "_" not in s else s.split("_")[0].split(".")[-1]
    return s[:1].upper() + s[1:] if s else "Player"


def _seconds(td):
    try:
        return td.total_seconds()
    except AttributeError:
        return float(getattr(td, "duration", 0) or 0) / 1e7


async def _snapshot():
    M = _mc()
    mgr = await M.request_async()
    cur = mgr.get_current_session()
    out = []
    for s in mgr.get_sessions():
        try:
            props = await s.try_get_media_properties_async()
            info = s.get_playback_info()
            tl = s.get_timeline_properties()
            status = int(info.playback_status)  # 4 playing, 5 paused
            pos, end = _seconds(tl.position), _seconds(tl.end_time)
            # the position is only as fresh as the player's last report
            try:
                age = max(0.0, time.time() - tl.last_updated_time.timestamp()) if status == 4 else 0
            except (AttributeError, OSError, ValueError, OverflowError):
                age = 0
            out.append({"id": s.source_app_user_model_id, "app": _app_name(s.source_app_user_model_id),
                        "title": props.title or "", "artist": props.artist or props.album_artist or "",
                        "state": "playing" if status == 4 else "paused" if status == 5 else "idle",
                        "position": pos + age, "duration": end,
                        "current": cur is not None and s.source_app_user_model_id == cur.source_app_user_model_id})
        except Exception:
            continue
    return out


def sessions():
    with _lock:
        hit = _cache.get("s")
        if hit and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
    got = _run(_snapshot())
    with _lock:
        _cache["s"] = (time.monotonic(), got)
    return got


def pick(items, app):
    app = (app or "").strip().lower()
    if app:
        return next((s for s in items if s["app"].lower() == app or app in s["id"].lower()), None)
    return (next((s for s in items if s["current"]), None) or next((s for s in items if s["state"] == "playing"), None)
            or (items[0] if items else None))


async def _control(app, what):
    M = _mc()
    mgr = await M.request_async()
    all_ = list(mgr.get_sessions())
    cur = mgr.get_current_session()
    target = None
    a = (app or "").strip().lower()
    for s in all_:
        name = _app_name(s.source_app_user_model_id).lower()
        if a and (name == a or a in s.source_app_user_model_id.lower()):
            target = s
            break
    if not a:
        target = cur or (all_[0] if all_ else None)
    if target is None:
        raise OSError(f"{app} isn't playing anything right now." if a else "Nothing is playing right now.")
    fn = {"toggle": target.try_toggle_play_pause_async, "play": target.try_play_async, "pause": target.try_pause_async,
          "next": target.try_skip_next_async, "prev": target.try_skip_previous_async, "stop": target.try_stop_async}[what]
    ok = await fn()
    if not ok:
        raise OSError(f"{_app_name(target.source_app_user_model_id)} didn't accept that.")


def _short(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


class NowPlaying(Plugin):
    id = "nowplaying"
    name = "Now playing (Windows)"
    description = "See and control whatever is playing on this PC: Spotify, browser tabs, VLC and any other media app."
    buttons = [{"id": "test", "label": "Check", "api": "test"}]
    actions = {
        "toggle": {"label": "Play / pause", "params": [APP]},
        "next": {"label": "Next", "params": [APP]},
        "prev": {"label": "Previous", "params": [APP]},
        "play": {"label": "Play", "params": [APP]},
        "pause": {"label": "Pause", "params": [APP]},
        "stop": {"label": "Stop", "params": [APP]},
    }
    widgets = {
        "now": {"label": "Now playing", "interval": 2, "params": [APP],
                "sample": {"icon": "▶", "text": "Midnight C…", "label": "The Artist", "progress": 0.4}},
        "app": {"label": "Which app is playing", "interval": 3, "params": [], "sample": {"icon": "♪", "text": "Spotify", "label": "Playing"}},
    }

    def run(self, action, params, ctx):
        if action not in self.actions:
            raise ValueError(f"unknown Now playing action {action!r}")
        _run(_control(params.get("app"), action))
        with _lock:
            _cache.clear()

    def widget(self, name, params, ctx):
        if name not in self.widgets:
            return super().widget(name, params, ctx)
        s = pick(sessions(), params.get("app") if name == "now" else "")
        if s is None or (not s["title"] and s["state"] == "idle"):
            return {"icon": "♪", "text": "—", "label": "Nothing playing", "on": False}
        if name == "app":
            return {"icon": "▶" if s["state"] == "playing" else "⏸", "text": _short(s["app"], 11),
                    "label": "Playing" if s["state"] == "playing" else "Paused", "on": s["state"] == "playing"}
        out = {"icon": "▶" if s["state"] == "playing" else "⏸", "text": _short(s["title"] or s["app"], 11),
               "label": _short(s["artist"] or s["app"], 14), "on": s["state"] == "playing"}
        if s["duration"] > 0:
            out["progress"] = round(max(0.0, min(1.0, s["position"] / s["duration"])), 3)
        return out

    def api(self, name, payload, ctx):
        if name == "test":
            try:
                items = sessions()
            except OSError as e:
                return {"ok": False, "message": str(e)}
            if not items:
                return {"ok": True, "message": "Working. Nothing is playing right now."}
            return {"ok": True, "message": "Working. Players: " + ", ".join(
                f"{s['app']} ({s['state']})" for s in items)}
        if name == "apps":
            out = [{"value": "", "label": "Whatever Windows is showing"}]
            try:
                seen = set()
                for s in sessions():
                    if s["app"] not in seen:
                        seen.add(s["app"])
                        out.append({"value": s["app"], "label": s["app"]})
            except OSError:
                pass
            return out
        return super().api(name, payload, ctx)


PLUGIN = NowPlaying()
