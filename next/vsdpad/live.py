"""Live keys: refreshes widget content (clock, weather, light state, ...) for the keys that are on screen.

Results are keyed by the widget spec, so two keys showing the same thing share one fetch. Only keys that some
connected device is showing are refreshed; each widget declares its own interval (at least 1 s).
"""
import json, re, threading, time
from concurrent.futures import ThreadPoolExecutor

from .actions import Context
from .log import LOG

ERROR_RETRY = 15
IMAGE_REF_RE = re.compile(r"^[a-z]{2,10}:[A-Za-z0-9_.-]{1,96}$")


def spec_id(live):
    return json.dumps({"p": live.get("plugin"), "w": live.get("widget"), "a": live.get("params", {})}, sort_keys=True)


class LiveEngine:
    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._results, self._due, self._inflight, self._errors = {}, {}, set(), {}
        self.version = 0
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="live")
        self._stop = threading.Event()

    def get(self, live):
        """Latest content for a live spec, or None if not fetched yet."""
        with self._lock:
            return self._results.get(spec_id(live))

    def sample(self, live):
        p = self.app.plugins.get(live.get("plugin"))
        w = (p.widgets.get(live.get("widget")) if p else None) or {}
        return dict(w.get("sample") or {"text": "…"})

    def poke(self, plugin_id):
        """Something changed (pushed by the plugin): refresh this plugin's live keys right away."""
        with self._lock:
            for sid in list(self._due):
                if json.loads(sid).get("p") == plugin_id:
                    self._due[sid] = 0

    def start(self):
        threading.Thread(target=self._loop, name="live", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._pool.shutdown(wait=False)

    def _loop(self):
        while not self._stop.wait(0.2):
            try:
                self.tick()
            except Exception:
                LOG.exception("live keys loop")

    def tick(self, now=None, wait=False):
        """Start refreshes that are due. wait=True runs them inline (tests)."""
        now = time.monotonic() if now is None else now
        for live in self.app.visible_live_specs():
            sid = spec_id(live)
            with self._lock:
                if sid in self._inflight or now < self._due.get(sid, 0):
                    continue
                self._inflight.add(sid)
            if wait:
                self._refresh(sid, live)
            else:
                self._pool.submit(self._refresh, sid, live)

    def _refresh(self, sid, live):
        plugin = self.app.plugins.get(live.get("plugin"))
        interval, result = ERROR_RETRY, None
        try:
            if plugin is None:
                raise ValueError(f"plugin {live.get('plugin')!r} is not installed")
            w = plugin.widgets.get(live.get("widget"))
            if w is None:
                raise ValueError(f"{plugin.name} has no live content {live.get('widget')!r}")
            ctx = Context(self.app, settings=self.app.store.plugin_settings(plugin.id))
            result = clean(plugin.widget(live["widget"], live.get("params", {}), ctx))
            interval = max(1.0, float(w.get("interval", 5)))
            self._errors.pop(sid, None)
        except Exception as e:
            if self._errors.get(sid) != str(e):
                LOG.warning("live key %s.%s: %s", live.get("plugin"), live.get("widget"), e)
            self._errors[sid] = str(e)
            result = {"icon": "⚠", "label": "error", "error": str(e)[:200]}
        finally:
            with self._lock:
                self._inflight.discard(sid)
                self._due[sid] = time.monotonic() + interval
                if result is not None and self._results.get(sid) != result:
                    self._results[sid] = result
                    self.version += 1


def clean(r):
    """Keep only known fields with sane types."""
    if not isinstance(r, dict):
        return {}
    out = {}
    for k in ("text", "label", "icon", "sub"):
        if r.get(k) is not None:
            out[k] = str(r[k])[:32]
    if isinstance(r.get("on"), bool):
        out["on"] = r["on"]
    c = r.get("color")
    if isinstance(c, (list, tuple)) and len(c) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in c):
        out["color"] = list(c)
    if isinstance(r.get("image"), str) and IMAGE_REF_RE.match(r["image"]):
        out["image"] = r["image"]  # a picture for the key, e.g. "raccoon:music" (when the key has none of its own)
    p = r.get("progress")
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        out["progress"] = max(0.0, min(1.0, float(p)))
    return out
