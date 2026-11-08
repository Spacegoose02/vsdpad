"""Day and night: change the theme and brightness at sunrise and sunset (or at set times).

Config (all optional):
  "daynight": {"enabled": true, "mode": "sun" | "times", "place": "London",
               "day_start": "07:00", "night_start": "20:00",           # used in "times" mode, or until the place is found
               "day_theme": "", "night_theme": "campfire",             # "" = keep the normal theme
               "day_brightness": 80, "night_brightness": 25,            # null = keep the normal brightness
               "fade_min": 30}                                          # brightness eases over this many minutes
The place falls back to the Weather plugin's town. Sunrise/sunset are worked out locally (NOAA formula); only finding
the town needs the internet, once.
"""
import datetime as dt, math, threading, time

from .log import LOG

RECHECK_S = 20


def sun_times(lat, lon, day):
    """(sunrise, sunset) as UTC datetimes for a date, "day" if the sun never sets, "night" if it never rises."""
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    cosha = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    if cosha > 1:
        return "night"
    if cosha < -1:
        return "day"
    ha = math.degrees(math.acos(cosha))
    midnight = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    return (midnight + dt.timedelta(minutes=720 - 4 * (lon + ha) - eqtime),
            midnight + dt.timedelta(minutes=720 - 4 * (lon - ha) - eqtime))


def _hhmm(s, default):
    try:
        h, m = str(s).split(":")
        return dt.time(int(h) % 24, int(m) % 60)
    except (ValueError, TypeError):
        return default


def daylight(now, rise, set_, fade_min):
    """0 at night, 1 by day, easing linearly over fade_min minutes centred on sunrise and sunset."""
    half = dt.timedelta(minutes=max(1, fade_min) / 2)
    span = 2 * half.total_seconds()
    up = ((now - (rise - half)).total_seconds()) / span
    down = (((set_ + half) - now).total_seconds()) / span
    if set_ <= rise:  # night start before day start (e.g. day 20:00-07:00): daylight wraps midnight
        return max(0.0, min(1.0, max(up, down)))
    return max(0.0, min(1.0, up, down))


class DayNight:
    def __init__(self, app=None):
        self.app = app
        self._geo, self._looking, self.problem = {}, set(), ""
        self._cache, self._lock = (None, 0, None), threading.Lock()

    # ---- where ----------------------------------------------------------------------------
    def _place(self, cfg):
        place = (cfg.get("daynight", {}).get("place") or "").strip()
        if not place:
            place = (cfg.get("plugins", {}).get("weather", {}).get("place") or "").strip()
        return place

    def _locate(self, place):
        """(lat, lon, name) once known; looks the place up in the background the first time."""
        if place in self._geo:
            return self._geo[place]
        weather = (self.app.plugins.get("weather") if self.app else None)
        if weather is None or place in self._looking:
            return None
        self._looking.add(place)

        def look():
            try:
                self._geo[place] = weather._locate(place)
                self.problem = ""
            except Exception as e:  # offline or unknown town: keep using the set times
                self.problem = str(e)[:200]
                LOG.info("day/night: %s", self.problem)
                time.sleep(300)
            finally:
                self._looking.discard(place)
        threading.Thread(target=look, name="daynight-geo", daemon=True).start()
        return None

    # ---- when -----------------------------------------------------------------------------
    def times(self, cfg, now=None):
        """(sunrise, sunset, source) as aware local datetimes for today."""
        dn = cfg.get("daynight", {})
        now = now or dt.datetime.now().astimezone()
        if dn.get("mode", "sun") == "sun":
            place = self._place(cfg)
            loc = self._locate(place) if place else None
            if loc:
                st = sun_times(loc[0], loc[1], now.date())
                if st in ("day", "night"):
                    return st, st, loc[2]
                return st[0].astimezone(now.tzinfo), st[1].astimezone(now.tzinfo), loc[2]
        rise, set_ = _hhmm(dn.get("day_start"), dt.time(7)), _hhmm(dn.get("night_start"), dt.time(20))
        return (now.replace(hour=rise.hour, minute=rise.minute, second=0, microsecond=0),
                now.replace(hour=set_.hour, minute=set_.minute, second=0, microsecond=0), "")

    def compute(self, cfg, now=None):
        """{"theme": id or None, "brightness": int or None, "day": bool, "light": 0-1}."""
        dn = cfg.get("daynight") or {}
        if not dn.get("enabled"):
            return {"theme": None, "brightness": None, "day": True, "light": 1.0}
        now = now or dt.datetime.now().astimezone()
        rise, set_, _ = self.times(cfg, now)
        if rise in ("day", "night"):
            light = 1.0 if rise == "day" else 0.0
        else:
            light = daylight(now, rise, set_, dn.get("fade_min", 30))
        day = light >= 0.5
        theme = (dn.get("day_theme") if day else dn.get("night_theme")) or None
        db, nb = dn.get("day_brightness"), dn.get("night_brightness")
        if db is None and nb is None:
            bright = None
        else:
            base = cfg.get("brightness", 80)
            db, nb = base if db is None else db, base if nb is None else nb
            bright = int(round(nb + (db - nb) * light))
        return {"theme": theme, "brightness": bright, "day": day, "light": round(light, 3)}

    def state(self, cfg):
        """compute(), rechecked at most every RECHECK_S seconds (or when the config changes)."""
        with self._lock:
            c, t, val = self._cache
            if c is cfg and time.monotonic() - t < RECHECK_S:
                return val
        try:
            val = self.compute(cfg)
        except Exception:
            LOG.exception("day/night")
            val = {"theme": None, "brightness": None, "day": True, "light": 1.0}
        with self._lock:
            self._cache = (cfg, time.monotonic(), val)
        return val

    def info(self, cfg):
        """For the settings page: what's happening now and when it changes."""
        dn = cfg.get("daynight") or {}
        try:
            rise, set_, where = self.times(cfg)
        except Exception:
            rise, set_, where = None, None, ""
        fmt = lambda t: t.strftime("%H:%M") if isinstance(t, dt.datetime) else (t or "")  # noqa: E731
        return {"enabled": bool(dn.get("enabled")), "sunrise": fmt(rise), "sunset": fmt(set_), "where": where,
                "place": self._place(cfg), "problem": self.problem, "now": self.compute(cfg)}
