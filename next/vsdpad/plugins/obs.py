"""OBS Studio: switch scenes, start/stop recording and streaming, mute inputs, show/hide sources, save replays.

Talks to OBS's built-in WebSocket server (OBS 28+: Tools → WebSocket Server Settings, port 4455).
One connection is kept open and shared; it reconnects by itself when OBS restarts. Live keys update the moment
OBS changes (scene switched, recording started, mic muted), not on a timer.
"""
import base64, hashlib, itertools, threading, time

from .base import Plugin
from .. import ws as wslib
from ..log import LOG

TIMEOUT = 5
RECONNECT_S = 5

SCENE = {"key": "scene", "label": "Scene", "kind": "choice", "api": "scenes"}
INPUT = {"key": "input", "label": "Audio input", "kind": "choice", "api": "inputs",
         "help": "e.g. Mic/Aux or Desktop Audio"}
SOURCE = {"key": "source", "label": "Source", "kind": "choice", "api": "sources"}


class OBSError(Exception):
    pass


def _auth(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def _tc(ms):
    s = int((ms or 0) / 1000)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Connection:
    """One live link to OBS. request() is thread-safe; events update self.state and call on_change."""

    def __init__(self, host, port, password, on_change=None):
        self.key = (host, port, password)
        self.host, self.port, self.password, self.on_change = host, port, password, on_change
        self.ws, self.ok, self.error = None, False, ""
        self._lock, self._ids = threading.Lock(), itertools.count(1)
        self._waiting = {}     # request id -> [Event, response]
        self.state = {}        # cached: scene, record, stream, mutes
        self._last_try = 0
        self._stopped = False

    def ensure(self):
        with self._lock:
            if self.ok:
                return
            if time.monotonic() - self._last_try < 1.0 and self.error:
                raise OBSError(self.error)
            self._last_try = time.monotonic()
            try:
                self._connect()
            except OBSError as e:
                self.error = str(e)
                raise
            except (OSError, ValueError) as e:
                self.error = f"Can't reach OBS at {self.host}:{self.port}. Is OBS open with the WebSocket server on? ({e})"
                raise OBSError(self.error) from None

    def _connect(self):
        ws = wslib.WebSocket(f"ws://{self.host}:{self.port}", timeout=TIMEOUT)
        hello = ws.recv(timeout=TIMEOUT)
        if not isinstance(hello, dict) or hello.get("op") != 0:
            ws.close()
            raise OBSError("That doesn't look like OBS's WebSocket server.")
        ident = {"rpcVersion": 1, "eventSubscriptions": (1 << 0) | (1 << 2) | (1 << 3) | (1 << 6) | (1 << 7)}
        a = hello["d"].get("authentication")
        if a:
            if not self.password:
                ws.close()
                raise OBSError("OBS wants a password: add it in Plugins → OBS (OBS: Tools → WebSocket Server Settings).")
            ident["authentication"] = _auth(self.password, a["salt"], a["challenge"])
        ws.send({"op": 1, "d": ident})
        try:
            got = ws.recv(timeout=TIMEOUT)
        except wslib.WSClosed:
            ws.close()
            raise OBSError("OBS didn't accept the password.") from None
        if not isinstance(got, dict) or got.get("op") != 2:
            ws.close()
            raise OBSError("OBS didn't accept the password.")
        ws.sock.settimeout(None)
        self.ws, self.ok, self.error = ws, True, ""
        threading.Thread(target=self._reader, args=(ws,), name="obs", daemon=True).start()
        self.state = {}
        LOG.info("OBS connected (%s:%s)", self.host, self.port)

    def _reader(self, ws):
        try:
            while True:
                m = ws.recv()
                if not isinstance(m, dict):
                    continue
                if m.get("op") == 7:
                    d = m.get("d", {})
                    w = self._waiting.get(d.get("requestId"))
                    if w:
                        w[1] = d
                        w[0].set()
                elif m.get("op") == 5:
                    self._event(m.get("d", {}))
        except (OSError, ValueError):
            pass
        finally:
            with self._lock:
                if self.ws is ws:
                    self.ok, self.ws = False, None
                    self.error = "OBS closed the connection."
            for w in list(self._waiting.values()):
                w[0].set()
            if not self._stopped:
                LOG.info("OBS disconnected")
                self._changed()

    def _event(self, d):
        t, data = d.get("eventType"), d.get("eventData") or {}
        st = self.state
        if t == "CurrentProgramSceneChanged":
            st["scene"] = data.get("sceneName")
        elif t == "RecordStateChanged":
            st["record"] = None
        elif t == "StreamStateChanged":
            st["stream"] = None
        elif t == "InputMuteStateChanged":
            st.setdefault("mutes", {})[data.get("inputName")] = data.get("inputMuted")
        elif t in ("SceneItemEnableStateChanged", "ReplayBufferStateChanged", "VirtualcamStateChanged",
                   "SceneListChanged", "InputNameChanged"):
            pass
        else:
            return
        self._changed()

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def request(self, rtype, data=None):
        self.ensure()
        rid = str(next(self._ids))
        w = [threading.Event(), None]
        self._waiting[rid] = w
        try:
            ws = self.ws
            if ws is None:
                raise OBSError(self.error or "OBS isn't connected.")
            try:
                ws.send({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data or {}}})
            except OSError:
                raise OBSError("Lost the connection to OBS.") from None
            if not w[0].wait(TIMEOUT) or w[1] is None:
                raise OBSError(self.error or "OBS didn't answer.")
        finally:
            self._waiting.pop(rid, None)
        st = w[1].get("requestStatus", {})
        if not st.get("result"):
            raise OBSError(f"OBS: {st.get('comment') or 'request failed'} ({rtype})")
        return w[1].get("responseData") or {}

    def close(self):
        self._stopped = True
        if self.ws:
            self.ws.close()


_conns, _conns_lock = {}, threading.Lock()


def _conn(settings, app=None):
    host = (settings.get("host") or "localhost").strip() or "localhost"
    try:
        port = int(settings.get("port") or 4455)
    except ValueError:
        raise OBSError("The OBS port must be a number (usually 4455).") from None
    key = (host, port, settings.get("password") or "")
    with _conns_lock:
        c = _conns.get("obs")
        if c is None or c.key != key:
            if c:
                c.close()
            poke = (lambda: app.live.poke("obs")) if app is not None and getattr(app, "live", None) else None
            c = _conns["obs"] = Connection(host, port, key[2], poke)
        elif c.on_change is None and app is not None and getattr(app, "live", None):
            c.on_change = lambda: app.live.poke("obs")
    return c


class OBS(Plugin):
    id = "obs"
    name = "OBS"
    description = "Switch scenes, record, stream, mute and show/hide sources in OBS Studio, with live keys."
    settings = [
        {"key": "host", "label": "OBS computer", "placeholder": "localhost",
         "help": "Leave as localhost when OBS runs on this PC."},
        {"key": "port", "label": "Port", "placeholder": "4455"},
        {"key": "password", "label": "WebSocket password", "secret": True,
         "help": "In OBS: Tools → WebSocket Server Settings → turn on “Enable WebSocket server”, then “Show Connect Info”."},
    ]
    buttons = [{"id": "test", "label": "Test connection", "api": "test"}]
    actions = {
        "scene": {"label": "Switch scene", "params": [SCENE]},
        "record": {"label": "Recording", "params": [{"key": "do", "label": "Do", "kind": "select", "options": [
            {"value": "toggle", "label": "Start / stop"}, {"value": "start", "label": "Start"},
            {"value": "stop", "label": "Stop"}, {"value": "pause", "label": "Pause / resume"}]}]},
        "stream": {"label": "Streaming", "params": [{"key": "do", "label": "Do", "kind": "select", "options": [
            {"value": "toggle", "label": "Start / stop"}, {"value": "start", "label": "Start"}, {"value": "stop", "label": "Stop"}]}]},
        "mute": {"label": "Mute / unmute", "params": [INPUT, {"key": "do", "label": "Do", "kind": "select", "options": [
            {"value": "toggle", "label": "Toggle"}, {"value": "mute", "label": "Mute"}, {"value": "unmute", "label": "Unmute"}]}]},
        "source": {"label": "Show / hide source", "params": [
            {**SCENE, "label": "In scene", "api": "scenes_current",
             "help": "Leave on “Current scene” to use whichever scene is live."}, SOURCE]},
        "replay": {"label": "Save replay", "params": []},
        "vcam": {"label": "Virtual camera on / off", "params": []},
        "studio": {"label": "Studio mode: send preview live", "params": []},
    }
    widgets = {
        "scene": {"label": "Scene", "interval": 10, "params": [
            {**SCENE, "label": "Light up for scene", "api": "scenes_current",
             "help": "Pick a scene to light this key while it's live, or leave it to show the live scene's name."}],
                  "sample": {"icon": "🎬", "text": "Gaming", "label": "Live", "on": True}},
        "record": {"label": "Recording", "interval": 1, "params": [], "sample": {"icon": "⏺", "text": "12:04", "label": "REC", "on": True}},
        "stream": {"label": "Streaming", "interval": 1, "params": [], "sample": {"icon": "📡", "text": "1:02:11", "label": "LIVE", "on": True}},
        "mute": {"label": "Mic / input muted", "interval": 10, "params": [INPUT], "sample": {"icon": "🎙", "label": "Mic", "on": True}},
    }

    def run(self, action, params, ctx):
        c = _conn(ctx.settings, ctx.app)
        do = params.get("do") or "toggle"
        if action == "scene":
            if not params.get("scene"):
                raise OBSError("Pick a scene for this key.")
            c.request("SetCurrentProgramScene", {"sceneName": params["scene"]})
        elif action == "record":
            c.request({"toggle": "ToggleRecord", "start": "StartRecord", "stop": "StopRecord",
                       "pause": "ToggleRecordPause"}.get(do, "ToggleRecord"))
        elif action == "stream":
            c.request({"toggle": "ToggleStream", "start": "StartStream", "stop": "StopStream"}.get(do, "ToggleStream"))
        elif action == "mute":
            if not params.get("input"):
                raise OBSError("Pick an audio input for this key.")
            if do == "toggle":
                c.request("ToggleInputMute", {"inputName": params["input"]})
            else:
                c.request("SetInputMute", {"inputName": params["input"], "inputMuted": do == "mute"})
        elif action == "source":
            if not params.get("source"):
                raise OBSError("Pick a source for this key.")
            scene = params.get("scene") or c.request("GetCurrentProgramScene").get("currentProgramSceneName")
            item = c.request("GetSceneItemId", {"sceneName": scene, "sourceName": params["source"]})["sceneItemId"]
            on = c.request("GetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item}).get("sceneItemEnabled")
            c.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item, "sceneItemEnabled": not on})
        elif action == "replay":
            c.request("SaveReplayBuffer")
        elif action == "vcam":
            c.request("ToggleVirtualCam")
        elif action == "studio":
            c.request("TriggerStudioModeTransition")
        else:
            raise ValueError(f"unknown OBS action {action!r}")

    def widget(self, name, params, ctx):
        try:
            c = _conn(ctx.settings, ctx.app)
            c.ensure()
        except OBSError:
            off = {"scene": "🎬", "record": "⏺", "stream": "📡", "mute": "🎙"}.get(name, "🎬")
            return {"icon": off, "text": "OBS off", "on": False}
        st = c.state
        if name == "scene":
            if st.get("scene") is None:
                st["scene"] = c.request("GetCurrentProgramScene").get("currentProgramSceneName")
            want = params.get("scene")
            if want:
                return {"icon": "🎬", "text": want[:11], "label": "Live" if st["scene"] == want else "", "on": st["scene"] == want}
            return {"icon": "🎬", "text": (st["scene"] or "?")[:11], "label": "Live", "on": True}
        if name in ("record", "stream"):
            r = c.request("GetRecordStatus" if name == "record" else "GetStreamStatus")
            active = bool(r.get("outputActive"))
            paused = bool(r.get("outputPaused"))
            icon = "⏺" if name == "record" else "📡"
            if not active:
                return {"icon": icon, "text": "Off", "label": "Record" if name == "record" else "Stream", "on": False}
            return {"icon": "⏸" if paused else icon, "text": _tc(r.get("outputDuration")),
                    "label": ("PAUSED" if paused else "REC") if name == "record" else "LIVE", "on": True,
                    "color": [255, 200, 0] if paused else [255, 60, 60]}
        if name == "mute":
            inp = params.get("input")
            if not inp:
                return {"icon": "🎙", "text": "?", "label": "Pick input", "on": False}
            mutes = st.setdefault("mutes", {})
            if mutes.get(inp) is None:
                mutes[inp] = c.request("GetInputMute", {"inputName": inp}).get("inputMuted")
            muted = bool(mutes[inp])
            return {"icon": "🔇" if muted else "🎙", "label": "Muted" if muted else inp[:14], "on": not muted,
                    "color": [255, 70, 70] if muted else None}
        return super().widget(name, params, ctx)

    def api(self, name, payload, ctx):
        s = ctx.settings
        if name == "test":
            try:
                c = _conn(s, ctx.app)
                v = c.request("GetVersion")
                scene = c.request("GetCurrentProgramScene").get("currentProgramSceneName")
                return {"ok": True, "message": f"Connected to OBS {v.get('obsVersion', '')}. Live scene: {scene}."}
            except OBSError as e:
                return {"ok": False, "message": str(e)}
        try:
            c = _conn(s, ctx.app)
            if name in ("scenes", "scenes_current"):
                scenes = [x.get("sceneName") for x in reversed(c.request("GetSceneList").get("scenes") or [])]
                first = [{"value": "", "label": "Current scene"}] if name == "scenes_current" else []
                return first + [{"value": n, "label": n} for n in scenes if n]
            if name == "inputs":
                out = []
                for i in c.request("GetInputList").get("inputs") or []:
                    try:
                        c.request("GetInputMute", {"inputName": i["inputName"]})
                    except OBSError:
                        continue   # not an audio input
                    out.append({"value": i["inputName"], "label": i["inputName"]})
                return out
            if name == "sources":
                scene = ((payload or {}).get("params") or {}).get("scene") or c.request("GetCurrentProgramScene").get("currentProgramSceneName")
                items = c.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems") or []
                return [{"value": i["sourceName"], "label": i["sourceName"]} for i in items if i.get("sourceName")]
        except OBSError:
            return [{"value": "", "label": "Current scene"}] if name == "scenes_current" else []
        return super().api(name, payload, ctx)


PLUGIN = OBS()
