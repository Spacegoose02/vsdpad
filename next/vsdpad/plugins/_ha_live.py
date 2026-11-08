"""Instant Home Assistant updates: one WebSocket to HA that keeps every entity's state and tells the live keys
the moment something changes (a light switched from the HA app, a door opened). Falls back to the REST API
while the WebSocket isn't connected, so keys keep working either way.
"""
import threading, time, urllib.parse

from .. import ws as wslib
from ..log import LOG

BACKOFF_MAX = 60


class HALive:
    def __init__(self, url, token):
        self.key = (url, token)
        u = urllib.parse.urlparse(url)
        scheme = "wss" if u.scheme == "https" else "ws"
        self.ws_url = f"{scheme}://{u.netloc}{u.path.rstrip('/')}/api/websocket"
        self.token = token
        self.states, self.ready, self.error = {}, False, ""
        self.entered = {}   # (entity, state) -> time.time() it last changed to that state (for alert keys)
        self.on_change = None
        self._stop = threading.Event()
        self._ws = None
        threading.Thread(target=self._loop, name="ha-live", daemon=True).start()

    def get(self, entity):
        """The cached state dict, or None if the link isn't up (use REST then)."""
        return self.states.get(entity) if self.ready else None

    def stop(self):
        self._stop.set()
        if self._ws:
            self._ws.close()

    def _loop(self):
        backoff = 2
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 2
            except PermissionError as e:
                self.error = str(e)
                LOG.warning("Home Assistant live updates: %s", e)
                backoff = BACKOFF_MAX
            except (OSError, ValueError, KeyError, TypeError) as e:
                if self.ready or not self.error:
                    LOG.info("Home Assistant live updates paused (%s), using polling until it's back", e)
                self.error = str(e) or "disconnected"
            finally:
                self.ready = False
                self._ws = None
            self._stop.wait(backoff)
            backoff = min(BACKOFF_MAX, backoff * 2)

    def _session(self):
        ws = wslib.WebSocket(self.ws_url, timeout=10)
        self._ws = ws
        try:
            m = ws.recv(timeout=10)
            if m.get("type") != "auth_required":
                raise ValueError("not a Home Assistant WebSocket")
            ws.send({"type": "auth", "access_token": self.token})
            m = ws.recv(timeout=10)
            if m.get("type") != "auth_ok":
                raise PermissionError("Home Assistant rejected the token")
            ws.send({"id": 1, "type": "get_states"})
            ws.send({"id": 2, "type": "subscribe_events", "event_type": "state_changed"})
            ws.sock.settimeout(None)
            while not self._stop.is_set():
                m = ws.recv()
                if not isinstance(m, dict):
                    continue
                if m.get("id") == 1 and m.get("type") == "result":
                    self.states = {s["entity_id"]: s for s in (m.get("result") or []) if isinstance(s, dict)}
                    if not self.ready:
                        LOG.info("Home Assistant live updates on (%d entities)", len(self.states))
                    self.ready, self.error = True, ""
                    self._changed()
                elif m.get("type") == "event":
                    d = (m.get("event") or {}).get("data") or {}
                    ent = d.get("entity_id")
                    if ent:
                        if d.get("new_state") is None:
                            self.states.pop(ent, None)
                        else:
                            self.states[ent] = d["new_state"]
                            self.entered[(ent, str(d["new_state"].get("state")))] = time.time()
                        self._changed()
        finally:
            ws.close()

    def _changed(self):
        cb = self.on_change
        if cb:
            try:
                cb()
            except Exception:
                pass


_links, _lock = {}, threading.Lock()


def link(url, token, app=None):
    """The shared live link for this HA address + token (started on first use)."""
    key = (url, token)
    with _lock:
        cur = _links.get("ha")
        if cur is None or cur.key != key:
            if cur:
                cur.stop()
            cur = _links["ha"] = HALive(url, token)
        if app is not None and getattr(app, "live", None) is not None and cur.on_change is None:
            live = app.live
            cur.on_change = lambda: live.poke("homeassistant")
    return cur


def stop_all():
    with _lock:
        for l in _links.values():
            l.stop()
        _links.clear()
