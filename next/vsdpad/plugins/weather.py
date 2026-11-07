"""Live key: the current weather from Open-Meteo (free, no account or key needed)."""
import json, urllib.parse, urllib.request

from .base import Plugin

TIMEOUT = 8
# WMO weather codes -> (symbol, short text)
CODES = {0: ("☀", "Clear"), 1: ("☀", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁", "Cloudy"),
         45: ("≡", "Fog"), 48: ("≡", "Fog"), 51: ("☂", "Drizzle"), 53: ("☂", "Drizzle"), 55: ("☂", "Drizzle"),
         61: ("☂", "Rain"), 63: ("☂", "Rain"), 65: ("☂", "Heavy rain"), 66: ("☂", "Icy rain"), 67: ("☂", "Icy rain"),
         71: ("❄", "Snow"), 73: ("❄", "Snow"), 75: ("❄", "Heavy snow"), 77: ("❄", "Snow"),
         80: ("☂", "Showers"), 81: ("☂", "Showers"), 82: ("☂", "Storm rain"), 85: ("❄", "Snow showers"),
         86: ("❄", "Snow showers"), 95: ("⚡", "Storm"), 96: ("⚡", "Storm"), 99: ("⚡", "Storm")}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vsdpad"}), timeout=TIMEOUT) as r:
        return json.loads(r.read())


class Weather(Plugin):
    id = "weather"
    name = "Weather"
    description = "A key with the temperature and sky right now (Open-Meteo, no account needed)."
    settings = [
        {"key": "place", "label": "Town or city", "placeholder": "London"},
        {"key": "units", "label": "Units (C or F)", "placeholder": "C"},
    ]
    buttons = [{"id": "test", "label": "Find my town", "api": "test"}]
    widgets = {"now": {"label": "Weather now", "interval": 600, "params": [],
                       "sample": {"text": "14°", "icon": "⛅", "label": "Partly cloudy"}}}

    def __init__(self):
        self._geo = {}

    def _locate(self, place):
        place = (place or "").strip()
        if not place:
            raise ValueError("Set your town in Plugins → Weather.")
        if place not in self._geo:
            r = _get("https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(place))
            res = (r or {}).get("results") or []
            if not res:
                raise ValueError(f"Couldn't find a place called {place!r}.")
            self._geo[place] = (res[0]["latitude"], res[0]["longitude"],
                                ", ".join(x for x in (res[0].get("name"), res[0].get("country")) if x))
        return self._geo[place]

    def widget(self, name, params, ctx):
        if name != "now":
            return super().widget(name, params, ctx)
        lat, lon, _ = self._locate(ctx.settings.get("place"))
        f = (ctx.settings.get("units") or "C").strip().upper().startswith("F")
        r = _get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                 f"&current=temperature_2m,weather_code&temperature_unit={'fahrenheit' if f else 'celsius'}")
        cur = r.get("current", {})
        sym, text = CODES.get(int(cur.get("weather_code", -1)), ("?", ""))
        return {"text": f"{round(cur.get('temperature_2m', 0))}°", "icon": sym, "label": text}

    def api(self, name, payload, ctx):
        if name == "test":
            try:
                self._geo.pop((ctx.settings.get("place") or "").strip(), None)
                _, _, where = self._locate(ctx.settings.get("place"))
                return {"ok": True, "message": f"Found {where}"}
            except Exception as e:
                return {"ok": False, "message": str(e)}
        return super().api(name, payload, ctx)


PLUGIN = Weather()
