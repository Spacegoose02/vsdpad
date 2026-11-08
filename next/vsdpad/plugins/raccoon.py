"""Raccoon mascot: an animated raccoon key that reacts to what's going on.

Moods, most important first: happy (just petted), live (OBS streaming or recording), music (something playing in
Windows "Now playing" or Wavelength), night (asleep: day/night's night, or the sleep hours set here), idle.
The live key gives the key its picture ("raccoon:<mood>", an animated GIF drawn by _raccoon_art on first use).
"""
import datetime as dt, os, tempfile, threading, time

from .base import Plugin
from . import _raccoon_art as art

ART_VERSION = 1
PET_S = 4
TEXT = {"happy": "Hi!", "live": "LIVE!", "music": "Vibing", "night": "Zzz", "idle": "Raccoon"}


def _hhmm(s, default):
    try:
        h, m = str(s).strip().split(":")
        return dt.time(int(h) % 24, int(m) % 60)
    except (ValueError, TypeError):
        return default


def asleep(now, start, end):
    """True if the time of day now is inside start..end (which may wrap past midnight)."""
    t = now.time()
    return (start <= t < end) if start <= end else (t >= start or t < end)


class Raccoon(Plugin):
    id = "raccoon"
    name = "Raccoon"
    description = ("An animated raccoon key: headphones when music plays, a LIVE badge when you stream or record, "
                   "asleep at night, and happy when you pet it.")
    settings = [
        {"key": "sleep_from", "label": "Goes to sleep at", "placeholder": "23:00",
         "help": "24-hour time. If Day and night is on in Settings, it sleeps at night instead."},
        {"key": "sleep_until", "label": "Wakes up at", "placeholder": "07:00"},
    ]
    actions = {"pet": {"label": "Pet the raccoon", "params": []}}
    widgets = {"mood": {"label": "Raccoon mascot", "interval": 2,
                        "params": [{"key": "names", "label": "Say what it's doing", "kind": "check", "default": False}],
                        "sample": {"image": "raccoon:music"}}}

    def __init__(self):
        self.pet_until = 0.0
        self.dir = os.path.join(tempfile.gettempdir(), f"vsdpad-raccoon-{ART_VERSION}")
        self._lock = threading.Lock()

    # ---- pictures -------------------------------------------------------------------------
    def image(self, ref):
        if ref not in art.MOODS:
            return None
        path = os.path.join(self.dir, ref + ".gif")
        if not os.path.exists(path):
            with self._lock:
                if not os.path.exists(path):
                    os.makedirs(self.dir, exist_ok=True)
                    tmp = path + f".{os.getpid()}.tmp"
                    art.gif(ref, tmp)
                    os.replace(tmp, path)
        return path

    # ---- what's going on ------------------------------------------------------------------
    def _other(self, ctx, plugin_id, widget, params=None):
        """Another plugin's live content, or None if it isn't set up or doesn't answer."""
        app = ctx.app
        p = app.plugins.get(plugin_id) if app else None
        if p is None or widget not in p.widgets:
            return None
        settings = app.store.plugin_settings(plugin_id)
        if p.settings and not any(settings.get(s["key"]) for s in p.settings):
            return None  # never set up: don't go knocking
        try:
            from ..actions import Context
            return p.widget(widget, params or {}, Context(app, settings=settings))
        except Exception:
            return None

    def _night(self, ctx, now):
        app = ctx.app
        if app is not None and getattr(app, "daynight", None) is not None:
            cfg = app.store.get()
            if (cfg.get("daynight") or {}).get("enabled"):
                return not app.daynight.state(cfg)["day"]
        return asleep(now, _hhmm(ctx.settings.get("sleep_from"), dt.time(23)),
                      _hhmm(ctx.settings.get("sleep_until"), dt.time(7)))

    def mood(self, ctx, now=None):
        now = now or dt.datetime.now()
        if time.monotonic() < self.pet_until:
            return "happy"
        if any((self._other(ctx, "obs", w) or {}).get("on") for w in ("stream", "record")):
            return "live"
        if any((self._other(ctx, pid, "now") or {}).get("on") for pid in ("nowplaying", "wavelength")):
            return "music"
        if self._night(ctx, now):
            return "night"
        return "idle"

    # ---- plugin ---------------------------------------------------------------------------
    def widget(self, name, params, ctx):
        if name != "mood":
            return super().widget(name, params, ctx)
        m = self.mood(ctx)
        out = {"image": f"raccoon:{m}"}
        if params.get("names"):
            out["label"] = TEXT[m]
        return out

    def run(self, action, params, ctx):
        if action != "pet":
            raise ValueError(f"Raccoon can't {action!r}")
        self.pet_until = time.monotonic() + PET_S
        if ctx.app is not None:
            ctx.app.live.poke(self.id)
            threading.Timer(PET_S + 0.1, ctx.app.live.poke, (self.id,)).start()  # back to its old self afterwards


PLUGIN = Raccoon()
