"""Countdown timers (e.g. Pomodoro). A key shows the countdown; tap actions start, pause or reset it."""
import threading, time

from .base import Plugin

NAME = {"key": "name", "label": "Timer name", "kind": "text", "default": "focus",
        "help": "Keys with the same name control and show the same timer."}


class Timer(Plugin):
    id = "timer"
    name = "Timers"
    description = "Countdown timers for focus sessions, tea, games. Start, pause and reset from keys."
    actions = {
        "start": {"label": "Start a timer", "params": [NAME, {"key": "minutes", "label": "Minutes", "kind": "number", "default": 25}]},
        "toggle": {"label": "Pause / resume (starts it if stopped)", "params": [
            NAME, {"key": "minutes", "label": "Minutes", "kind": "number", "default": 25}]},
        "reset": {"label": "Reset a timer", "params": [NAME]},
    }
    widgets = {"countdown": {"label": "Countdown", "interval": 1, "params": [NAME],
                             "sample": {"text": "24:59", "progress": 0.99}}}

    def __init__(self):
        self._lock = threading.Lock()
        self._timers = {}  # name -> {"total", "end" (running) | "left" (paused)}

    def _name(self, params):
        return (str(params.get("name") or "focus").strip() or "focus")[:24]

    def run(self, action, params, ctx, now=None):
        now = time.monotonic() if now is None else now
        name = self._name(params)
        total = max(1, min(24 * 60 * 60, int(float(params.get("minutes", 25)) * 60)))
        with self._lock:
            t = self._timers.get(name)
            if action == "start" or (action == "toggle" and t is None):
                self._timers[name] = {"total": total, "end": now + total}
            elif action == "toggle":
                if "end" in t:
                    t["left"] = max(0.0, t.pop("end") - now)
                elif t.get("left", 0) <= 0:
                    self._timers[name] = {"total": total, "end": now + total}
                else:
                    t["end"] = now + t.pop("left")
            elif action == "reset":
                self._timers.pop(name, None)
            else:
                raise ValueError(f"unknown timer action {action!r}")

    def widget(self, name, params, ctx, now=None):
        if name != "countdown":
            return super().widget(name, params, ctx)
        now = time.monotonic() if now is None else now
        with self._lock:
            t = dict(self._timers.get(self._name(params)) or {})
        if not t:
            return {"text": "--:--", "on": False}
        left = t["end"] - now if "end" in t else t.get("left", 0)
        if left <= 0:
            return {"text": "DONE", "on": int(now * 2) % 2 == 0, "progress": 0}  # blinks
        m, s = divmod(int(left + 0.999), 60)
        h, m = divmod(m, 60)
        text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        return {"text": text, "progress": round(left / t["total"], 3), "on": "end" in t,
                **({} if "end" in t else {"sub": "paused"})}


PLUGIN = Timer()
