"""Live keys: the time and the date."""
import time

from .base import Plugin


class Clock(Plugin):
    id = "clock"
    name = "Clock"
    description = "Keys that show the time or the date."
    widgets = {
        "time": {"label": "Time", "interval": 1, "sample": {"text": "21:45"}, "params": [
            {"key": "format", "label": "Format", "kind": "select", "default": "24",
             "options": [{"value": "24", "label": "24-hour (21:45)"}, {"value": "12", "label": "12-hour (9:45)"},
                         {"value": "24s", "label": "With seconds (21:45:07)"}]}]},
        "date": {"label": "Date", "interval": 30, "sample": {"text": "7 Nov", "label": "Saturday"}, "params": []},
    }

    def widget(self, name, params, ctx, now=None):
        t = time.localtime(now if now is not None else time.time())
        if name == "time":
            fmt = params.get("format", "24")
            if fmt == "12":
                return {"text": f"{(t.tm_hour % 12) or 12}:{t.tm_min:02d}", "sub": "AM" if t.tm_hour < 12 else "PM"}
            if fmt == "24s":
                return {"text": time.strftime("%H:%M:%S", t)}
            return {"text": time.strftime("%H:%M", t)}
        if name == "date":
            return {"text": f"{t.tm_mday} {time.strftime('%b', t)}", "label": time.strftime("%A", t)}
        return super().widget(name, params, ctx)


PLUGIN = Clock()
