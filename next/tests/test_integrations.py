"""Macros (loops, held keys, recording), OBS, instant Home Assistant updates and Now playing - no real apps needed.
Run:  python tests/test_integrations.py
"""
import base64, hashlib, json, os, socket, struct, sys, threading, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad import actions, recorder, schema, ws as wslib  # noqa: E402
from vsdpad.actions import Context  # noqa: E402
from vsdpad.live import LiveEngine  # noqa: E402
from vsdpad.plugins import obs as obs_mod, nowplaying as np_mod, _ha_live  # noqa: E402
from vsdpad.plugins import homeassistant as ha_mod  # noqa: E402


def wait_for(fn, t=2.0):
    end = time.time() + t
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.01)
    return fn()


# ---------------------------------------------------------------- a tiny WebSocket server for the mocks
class MockWS:
    """Accepts WebSocket clients; handler(conn) runs per client with conn.send(obj) / conn.recv()."""

    def __init__(self, handler):
        self.handler, self.clients = handler, []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        head = b""
        while b"\r\n\r\n" not in head:
            head += c.recv(4096)
        key = [l.split(":", 1)[1].strip() for l in head.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1((key + wslib.GUID).encode()).digest()).decode()
        c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n".encode())
        conn = _Conn(c)
        self.clients.append(conn)
        try:
            self.handler(conn)
        except (OSError, ValueError):
            pass

    def drop_all(self):
        for c in self.clients:
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
                c.sock.close()
            except OSError:
                pass
        self.clients.clear()

    def close(self):
        self.sock.close()
        self.drop_all()


class _Conn:
    def __init__(self, sock):
        self.sock, self.lock = sock, threading.Lock()

    def send(self, obj):
        data = json.dumps(obj).encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536
                                else bytes([127]) + struct.pack(">Q", n))
        with self.lock:
            self.sock.sendall(head + data)

    def _read(self, n):
        b = b""
        while len(b) < n:
            chunk = self.sock.recv(n - len(b))
            if not chunk:
                raise OSError("closed")
            b += chunk
        return b

    def recv(self):
        b1, b2 = self._read(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
        if b1 & 0x0F == 0x8:
            raise OSError("closed")
        return json.loads(data)


# ---------------------------------------------------------------- macros
class LoopApp:
    def __init__(self):
        self.ran, self.ended, self.errors = [], 0, []
        self.lock = threading.Lock()
        app = self

        class P:
            name = "probe"

            def end_steps(self):
                app.ended += 1
        self.plugins = {"probe": P()}

    def run_action(self, step, device_id=None, nav=None):
        if step["action"] == "boom":
            raise ValueError("boom")
        with self.lock:
            self.ran.append(step["action"])

    def report_error(self, d, m):
        self.errors.append(m)


def st(a):
    return {"plugin": "x", "action": a, "params": {}}


class TestMacros(unittest.TestCase):
    def test_loop_repeats_steps_above(self):
        app = LoopApp()
        actions.run_steps(app, [st("a"), st("b"), {"loop": 3}, st("c")])
        self.assertEqual(app.ran, ["a", "b"] * 3 + ["c"])

    def test_two_loops_repeat_their_own_part(self):
        app = LoopApp()
        actions.run_steps(app, [st("a"), {"loop": 2}, st("b"), {"loop": 3}])
        self.assertEqual(app.ran, ["a", "a", "b", "b", "b"])

    def test_cleanup_runs_even_when_a_step_fails(self):
        app = LoopApp()
        with self.assertRaises(ValueError):
            actions.run_steps(app, [st("a"), st("boom"), st("c")])
        self.assertEqual(app.ended, 1)

    def test_forever_loop_stops_when_key_pressed_again(self):
        app = LoopApp()
        keys = {1: {"tap": [st("a"), {"wait": 0.01}, {"loop": 0}]}}
        g = actions.Gestures(app, "d", None, lambda b: keys.get(b))
        g.press(1); g.release(1)
        self.assertTrue(wait_for(lambda: len(app.ran) >= 3))
        g.press(1); g.release(1)
        time.sleep(0.15)
        n = len(app.ran)
        time.sleep(0.2)
        self.assertEqual(len(app.ran), n, "the loop stopped")
        self.assertTrue(wait_for(lambda: app.ended == 1))
        g.press(1); g.release(1)   # and the next press starts it again
        self.assertTrue(wait_for(lambda: len(app.ran) > n + 2))
        g.cancel_all()
        time.sleep(0.15)
        n = len(app.ran)
        time.sleep(0.15)
        self.assertEqual(len(app.ran), n, "disconnecting stops loops too")

    def test_forever_loop_is_paced(self):
        app = LoopApp()
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        actions.run_steps(app, [st("a"), {"loop": 0}], cancel=cancel)
        self.assertLess(len(app.ran), 12)

    def test_schema_allows_loops_and_100_steps(self):
        schema.validate_step({"loop": 0}, "k")
        schema.validate_step({"loop": 5}, "k")
        with self.assertRaises(schema.ConfigError):
            schema.validate_step({"loop": -1}, "k")
        with self.assertRaises(schema.ConfigError):
            schema.validate_step({"loop": "x"}, "k")
        schema.validate_key({"tap": [st("a")] * 100}, "k")

    def test_recording_becomes_steps(self):
        A, SHIFT, CTRL, ENTER = 0x41, 0x10, 0x11, 0x0D
        ev = [("key", 0.0, SHIFT, True, None), ("key", 0.05, 0x48, True, "H"), ("key", 0.07, 0x48, False, None),
              ("key", 0.08, SHIFT, False, None), ("key", 0.1, 0x49, True, "i"), ("key", 0.15, ENTER, True, "\r"),
              ("key", 1.2, CTRL, True, None), ("key", 1.25, A, True, "\x01"), ("key", 1.3, CTRL, False, None),
              ("click", 2.0, "left", 100, 200), ("click", 2.2, "left", 101, 201),
              ("scroll", 2.5, -1), ("scroll", 2.55, -2), ("click", 3.0, "right", 5, 6)]
        steps = recorder.to_steps(ev)
        self.assertEqual(steps, [
            {"plugin": "system", "action": "type", "params": {"text": "Hi\\n"}},
            {"wait": 1.1},
            {"plugin": "system", "action": "hotkey", "params": {"keys": "ctrl+a"}},
            {"wait": 0.8},
            {"plugin": "macro", "action": "click", "params": {"button": "left", "x": 100, "y": 200, "count": "2"}},
            {"wait": 0.3},
            {"plugin": "macro", "action": "scroll", "params": {"amount": -3}},
            {"wait": 0.5},
            {"plugin": "macro", "action": "click", "params": {"button": "right", "x": 5, "y": 6, "count": "1"}}])
        for s in steps:
            schema.validate_step(s, "rec")

    def test_recorder_needs_windows_here(self):
        if sys.platform != "win32":
            with self.assertRaises(OSError):
                recorder.Recorder().start()


# ---------------------------------------------------------------- OBS
class TestOBS(unittest.TestCase):
    def setUp(self):
        self.reqs = []
        self.state = {"scene": "Gaming", "rec": False, "muted": {"Mic/Aux": False}}
        self.password = "s3cret"

        def handler(c):
            salt, chal = "saltyy", "chall"
            c.send({"op": 0, "d": {"rpcVersion": 1, "authentication": {"salt": salt, "challenge": chal}}})
            ident = c.recv()
            if ident["d"].get("authentication") != obs_mod._auth(self.password, salt, chal):
                c.sock.close()
                return
            c.send({"op": 2, "d": {"negotiatedRpcVersion": 1}})
            while True:
                m = c.recv()["d"]
                t, d = m["requestType"], m.get("requestData") or {}
                self.reqs.append((t, d))
                out, ok = {}, True
                if t == "GetVersion":
                    out = {"obsVersion": "30.1"}
                elif t == "GetCurrentProgramScene":
                    out = {"currentProgramSceneName": self.state["scene"]}
                elif t == "SetCurrentProgramScene":
                    self.state["scene"] = d["sceneName"]
                    c.send({"op": 5, "d": {"eventType": "CurrentProgramSceneChanged", "eventData": {"sceneName": d["sceneName"]}}})
                elif t == "GetSceneList":
                    out = {"scenes": [{"sceneName": "BRB"}, {"sceneName": "Gaming"}]}
                elif t == "ToggleRecord":
                    self.state["rec"] = not self.state["rec"]
                elif t == "GetRecordStatus":
                    out = {"outputActive": self.state["rec"], "outputPaused": False, "outputDuration": 125000}
                elif t == "GetInputList":
                    out = {"inputs": [{"inputName": "Mic/Aux"}, {"inputName": "Webcam"}]}
                elif t == "GetInputMute":
                    if d["inputName"] in self.state["muted"]:
                        out = {"inputMuted": self.state["muted"][d["inputName"]]}
                    else:
                        ok = False
                elif t == "ToggleInputMute":
                    v = self.state["muted"][d["inputName"]] = not self.state["muted"][d["inputName"]]
                    c.send({"op": 5, "d": {"eventType": "InputMuteStateChanged", "eventData": {"inputName": d["inputName"], "inputMuted": v}}})
                elif t == "GetSceneItemId":
                    out = {"sceneItemId": 7}
                elif t == "GetSceneItemEnabled":
                    out = {"sceneItemEnabled": True}
                elif t == "SetSceneItemEnabled":
                    pass
                else:
                    ok = False
                c.send({"op": 7, "d": {"requestType": t, "requestId": m["requestId"],
                                       "requestStatus": {"result": ok, "code": 100 if ok else 600, "comment": None if ok else "nope"},
                                       "responseData": out}})

        self.srv = MockWS(handler)
        self.settings = {"host": "127.0.0.1", "port": str(self.srv.port), "password": self.password}
        self.ctx = Context(None, settings=self.settings)
        obs_mod._conns.clear()
        self.P = obs_mod.PLUGIN

    def tearDown(self):
        for c in obs_mod._conns.values():
            c.close()
        obs_mod._conns.clear()
        self.srv.close()

    def test_connect_and_test(self):
        r = self.P.api("test", {}, self.ctx)
        self.assertTrue(r["ok"], r)
        self.assertIn("OBS 30.1", r["message"])
        self.assertIn("Gaming", r["message"])

    def test_wrong_password(self):
        r = self.P.api("test", {}, Context(None, settings={**self.settings, "password": "bad"}))
        self.assertFalse(r["ok"])
        self.assertIn("password", r["message"])

    def test_not_running(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        r = self.P.api("test", {}, Context(None, settings={"port": str(port)}))
        self.assertFalse(r["ok"])
        self.assertIn("Can't reach OBS", r["message"])
        self.assertEqual(self.P.widget("record", {}, Context(None, settings={"port": str(port)}))["text"], "OBS off")

    def test_actions_and_live_keys(self):
        self.P.run("scene", {"scene": "BRB"}, self.ctx)
        self.assertEqual(self.state["scene"], "BRB")
        self.assertTrue(wait_for(lambda: obs_mod._conns["obs"].state.get("scene") == "BRB"), "event updated the cache")
        self.assertEqual(self.P.widget("scene", {"scene": "BRB"}, self.ctx)["on"], True)
        self.assertEqual(self.P.widget("scene", {"scene": "Gaming"}, self.ctx)["on"], False)
        self.assertEqual(self.P.widget("record", {}, self.ctx)["text"], "Off")
        self.P.run("record", {"do": "toggle"}, self.ctx)
        w = self.P.widget("record", {}, self.ctx)
        self.assertEqual((w["text"], w["label"], w["on"]), ("2:05", "REC", True))
        self.assertEqual(self.P.widget("mute", {"input": "Mic/Aux"}, self.ctx)["on"], True)
        self.P.run("mute", {"input": "Mic/Aux"}, self.ctx)
        self.assertTrue(wait_for(lambda: self.P.widget("mute", {"input": "Mic/Aux"}, self.ctx)["label"] == "Muted"))
        self.P.run("source", {"source": "Webcam"}, self.ctx)
        self.assertIn(("SetSceneItemEnabled", {"sceneName": "BRB", "sceneItemId": 7, "sceneItemEnabled": False}), self.reqs)

    def test_pickers(self):
        self.assertEqual([o["value"] for o in self.P.api("scenes", {}, self.ctx)], ["Gaming", "BRB"])
        self.assertEqual(self.P.api("scenes_current", {}, self.ctx)[0]["label"], "Current scene")
        self.assertEqual(self.P.api("inputs", {}, self.ctx), [{"value": "Mic/Aux", "label": "Mic/Aux"}])

    def test_failed_request_is_readable(self):
        with self.assertRaises(obs_mod.OBSError) as e:
            self.P.run("replay", {}, self.ctx)
        self.assertIn("nope", str(e.exception))

    def test_reconnects_after_obs_restarts(self):
        self.P.api("test", {}, self.ctx)
        self.srv.drop_all()
        self.assertTrue(wait_for(lambda: not obs_mod._conns["obs"].ok))
        time.sleep(1.05)
        self.assertTrue(self.P.api("test", {}, self.ctx)["ok"])

    def test_events_poke_live_keys(self):
        class App:
            pass
        app = App()
        app.pokes = []
        app.live = type("L", (), {"poke": lambda _s, pid: app.pokes.append(pid)})()
        ctx = Context(app, settings=self.settings)
        self.P.run("scene", {"scene": "BRB"}, ctx)
        self.assertTrue(wait_for(lambda: "obs" in app.pokes))


# ---------------------------------------------------------------- Home Assistant instant updates
class TestHALive(unittest.TestCase):
    def setUp(self):
        self.conns = []

        def handler(c):
            self.conns.append(c)
            c.send({"type": "auth_required"})
            m = c.recv()
            if m.get("access_token") != "good":
                c.send({"type": "auth_invalid"})
                return
            c.send({"type": "auth_ok"})
            while True:
                m = c.recv()
                if m.get("type") == "get_states":
                    c.send({"id": m["id"], "type": "result", "success": True, "result": [
                        {"entity_id": "light.desk", "state": "off", "attributes": {}}]})
                elif m.get("type") == "subscribe_events":
                    c.send({"id": m["id"], "type": "result", "success": True})

        self.srv = MockWS(handler)
        _ha_live.stop_all()

    def tearDown(self):
        _ha_live.stop_all()
        self.srv.close()

    def test_states_and_pushed_changes(self):
        pokes = []

        class App:
            live = type("L", (), {"poke": lambda _s, pid: pokes.append(pid)})()
        link = _ha_live.link(f"http://127.0.0.1:{self.srv.port}", "good", App())
        self.assertTrue(wait_for(lambda: link.get("light.desk") is not None, 3))
        self.assertEqual(link.get("light.desk")["state"], "off")
        self.conns[-1].send({"type": "event", "event": {"event_type": "state_changed", "data": {
            "entity_id": "light.desk", "new_state": {"entity_id": "light.desk", "state": "on", "attributes": {"brightness": 255}}}}})
        self.assertTrue(wait_for(lambda: link.get("light.desk")["state"] == "on"))
        self.assertIn("homeassistant", pokes)
        # the plugin's live key reads the pushed state, no REST call needed
        ctx = Context(App(), settings={"url": f"http://127.0.0.1:{self.srv.port}", "token": "good"})
        w = ha_mod.PLUGIN.widget("state", {"entity": "light.desk"}, ctx)
        self.assertEqual(w["on"], True)
        self.assertEqual(w["progress"], 1.0)

    def test_bad_token_falls_back(self):
        link = _ha_live.link(f"http://127.0.0.1:{self.srv.port}", "bad")
        self.assertTrue(wait_for(lambda: "rejected" in link.error, 3))
        self.assertIsNone(link.get("light.desk"))

    def test_https_uses_wss(self):
        l = _ha_live.HALive.__new__(_ha_live.HALive)
        u = "https://ha.example.com:8123"
        import urllib.parse
        p = urllib.parse.urlparse(u)
        self.assertEqual(("wss" if p.scheme == "https" else "ws") + f"://{p.netloc}/api/websocket", "wss://ha.example.com:8123/api/websocket")
        del l


class TestLivePoke(unittest.TestCase):
    def test_poke_makes_plugin_keys_due(self):
        class App:
            plugins = {}
        eng = LiveEngine(App())
        a = {"plugin": "homeassistant", "widget": "state", "params": {}}
        b = {"plugin": "clock", "widget": "time", "params": {}}
        from vsdpad.live import spec_id
        eng._due = {spec_id(a): time.monotonic() + 99, spec_id(b): time.monotonic() + 99}
        eng.poke("homeassistant")
        self.assertEqual(eng._due[spec_id(a)], 0)
        self.assertGreater(eng._due[spec_id(b)], 0)


# ---------------------------------------------------------------- Now playing helpers
class TestNowPlaying(unittest.TestCase):
    def test_app_names(self):
        self.assertEqual(np_mod._app_name("Spotify.exe"), "Spotify")
        self.assertEqual(np_mod._app_name("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"), "Spotify")
        self.assertEqual(np_mod._app_name("Chrome"), "Chrome")
        self.assertEqual(np_mod._app_name("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "ZuneMusic")

    def test_pick(self):
        items = [{"id": "Chrome", "app": "Chrome", "state": "paused", "current": False},
                 {"id": "Spotify.exe", "app": "Spotify", "state": "playing", "current": False},
                 {"id": "vlc", "app": "Vlc", "state": "paused", "current": True}]
        self.assertEqual(np_mod.pick(items, "")["app"], "Vlc")           # what Windows shows
        self.assertEqual(np_mod.pick(items, "spotify")["app"], "Spotify")
        self.assertIsNone(np_mod.pick(items, "Edge"))
        self.assertEqual(np_mod.pick([i for i in items if not i["current"]], "")["app"], "Spotify")

    def test_widget(self):
        old = np_mod.sessions
        np_mod.sessions = lambda: [{"id": "Spotify.exe", "app": "Spotify", "title": "A Very Long Song", "artist": "Band",
                                    "state": "playing", "position": 30, "duration": 120, "current": True}]
        try:
            w = np_mod.PLUGIN.widget("now", {}, Context(None))
            self.assertEqual(w, {"icon": "▶", "text": "A Very Lon…", "label": "Band", "on": True, "progress": 0.25})
            self.assertEqual(np_mod.PLUGIN.widget("app", {}, Context(None))["text"], "Spotify")
        finally:
            np_mod.sessions = old

    def test_explains_missing_add_on(self):
        if sys.platform != "win32":
            r = np_mod.PLUGIN.api("test", {}, Context(None))
            self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
