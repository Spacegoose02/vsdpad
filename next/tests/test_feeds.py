"""Notification keys, stats keys, SUB/WAVE Up next and Wavelength handoff, against local mock servers.
Run:  python tests/test_feeds.py
"""
import json, os, sys, threading, time, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad.actions import Context  # noqa: E402
from vsdpad.plugins import stats, notify, subwave, wavelength, homeassistant as ha  # noqa: E402


def serve(routes, log):
    """routes: {(method, path): (code, obj) or callable(query, body) -> (code, obj)}"""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _go(self):
            u = urllib.parse.urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = urllib.parse.parse_qs(raw.decode())
            log.append((self.command, u.path, dict(urllib.parse.parse_qsl(u.query)), body, dict(self.headers)))
            r = routes.get((self.command, u.path), (404, {"error": "not found"}))
            code, obj = r(dict(urllib.parse.parse_qsl(u.query)), body, self.headers) if callable(r) else r
            data = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        do_GET = do_POST = do_DELETE = _go

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class TestStats(unittest.TestCase):
    def setUp(self):
        self.log = []
        games = {"response": {"games": [{"appid": 252490, "name": "Rust", "playtime_forever": 18720},
                                        {"appid": 10, "name": "Counter-Strike", "playtime_forever": 45}]}}

        def token(q, body, h):
            if body.get("client_secret") != ["sek"]:
                return 403, {"message": "invalid client secret"}
            return 200, {"access_token": "tok1", "expires_in": 5000}

        def streams(q, body, h):
            if h.get("Authorization") != "Bearer tok1" or h.get("Client-Id") != "cid":
                return 401, {}
            return 200, {"data": [{"viewer_count": 1234}]} if q.get("user_login") == "livechan" else {"data": []}

        routes = {
            ("GET", "/IPlayerService/GetOwnedGames/v1/"): lambda q, b, h: (200, games) if q.get("key") == "k" else (403, {}),
            ("GET", "/ISteamUser/ResolveVanityURL/v1/"): (200, {"response": {"success": 1, "steamid": "76561197960287930"}}),
            ("GET", "/IPlayerService/GetRecentlyPlayedGames/v1/"): (200, {"response": {"games": [{"playtime_2weeks": 600}, {"playtime_2weeks": 90}]}}),
            ("GET", "/ISteamUser/GetPlayerSummaries/v2/"): (200, {"response": {"players": [{"gameextrainfo": "Rust"}]}}),
            ("POST", "/token"): token,
            ("GET", "/helix/streams"): streams,
            ("GET", "/web"): (200, {"data": {"items": [{"count": 15300}]}}),
        }
        self.srv, url = serve(routes, self.log)
        self.old = (stats.STEAM, stats.TWITCH_ID, stats.TWITCH_API)
        stats.STEAM, stats.TWITCH_ID, stats.TWITCH_API = url, url + "/token", url + "/helix/"
        stats._cache.clear(); stats._token.clear()
        self.url = url
        self.s = {"steam_key": "k", "steam_id": "https://steamcommunity.com/id/gabe/", "twitch_id": "cid", "twitch_secret": "sek"}
        self.ctx = Context(None, settings=self.s)

    def tearDown(self):
        stats.STEAM, stats.TWITCH_ID, stats.TWITCH_API = self.old
        self.srv.shutdown()

    def w(self, name, params=None, ctx=None):
        return stats.PLUGIN.widget(name, params or {}, ctx or self.ctx)

    def test_steam(self):
        self.assertEqual(self.w("steam_game", {"appid": "252490"}), {"text": "312h", "label": "Rust"})
        self.assertEqual(self.w("steam_game", {"appid": "10"})["text"], "0.8h")
        self.assertEqual(self.w("steam_total")["text"], "12h")
        self.assertEqual(self.w("steam_now")["text"], "Rust")
        self.assertEqual([o["value"] for o in stats.PLUGIN.api("games", {}, self.ctx)], ["252490", "10"])
        # a profile link with a name is looked up once; a numeric id is used as is
        self.assertTrue(any(p == "/ISteamUser/ResolveVanityURL/v1/" and q["vanityurl"] == "gabe" for _, p, q, _, _ in self.log))
        self.assertEqual(stats._steam_id({"steam_id": "https://steamcommunity.com/profiles/76561198000000001"}), "76561198000000001")

    def test_steam_errors(self):
        with self.assertRaises(stats.StatsError) as e:
            self.w("steam_game", {"appid": "1"}, Context(None, settings={**self.s, "steam_key": "bad", "steam_id": "76561198000000009"}))
        self.assertIn("didn't accept", str(e.exception))
        with self.assertRaises(stats.StatsError) as e:
            self.w("steam_total", ctx=Context(None, settings={"steam_id": "x"}))
        self.assertIn("Steam Web API key", str(e.exception))

    def test_twitch(self):
        self.assertEqual(self.w("twitch", {"channel": "https://twitch.tv/LiveChan"})["text"], "1.2k")
        self.assertEqual(self.w("twitch", {"channel": "sleepy"}), {"icon": "📺", "text": "Offline", "label": "sleepy", "on": False})
        self.assertEqual(sum(1 for _, p, *_ in self.log if p == "/token"), 1, "the app token is reused")
        stats._token["cid"] = ("expired", time.time() + 9999)   # Twitch revoked it: get a new one and retry
        self.assertEqual(self.w("twitch", {"channel": "livechan"})["text"], "1.2k")
        r = stats.PLUGIN.api("test", {}, Context(None, settings={"twitch_id": "cid", "twitch_secret": "nope"}))
        self.assertFalse(r["ok"])

    def test_any_web_number(self):
        w = self.w("web", {"url": self.url + "/web", "path": "data.items.0.count", "label": "Followers"})
        self.assertEqual(w, {"text": "15k", "label": "Followers"})
        self.assertEqual(stats.pick({"a": [1, {"b": 2}]}, "a[1].b"), 2)
        self.assertEqual(stats.pick(7, ""), 7)
        with self.assertRaises(stats.StatsError):
            stats.pick({"a": 1}, "b")
        self.assertEqual(self.w("web", {"url": "file:///etc/passwd"})["label"], "Add address")

    def test_numbers(self):
        self.assertEqual([stats._num(x) for x in (5, 2.5, 1234, 15300, 2_500_000)], ["5", "2.5", "1.2k", "15k", "2.5M"])


class TestNotify(unittest.TestCase):
    def test_app_widget_and_clear(self):
        items = [{"id": 1, "app": "Discord", "title": "@you in #general", "body": "hi"},
                 {"id": 2, "app": "Outlook", "title": "Invoice", "body": ""},
                 {"id": 3, "app": "Discord", "title": "Sam: lunch?", "body": ""}]
        removed = []
        old = (notify.notifications, notify._run, notify._remove_async)
        notify.notifications = lambda: list(items)
        notify._remove_async = lambda ids: removed.extend(ids)
        notify._run = lambda x: x
        try:
            w = notify.PLUGIN.widget("app", {"app": "discord"}, Context(None))
            self.assertEqual((w["text"], w["label"], w["on"]), ("2", "Sam: lunch?", True))
            self.assertEqual(notify.PLUGIN.widget("app", {"app": "Slack"}, Context(None))["on"], False)
            self.assertEqual(notify.PLUGIN.widget("app", {}, Context(None))["text"], "3")
            notify.PLUGIN.run("clear", {"app": "Discord"}, Context(None))
            self.assertEqual(removed, [1, 3])
            apps = [o["value"] for o in notify.PLUGIN.api("apps", {}, Context(None))]
            self.assertEqual(apps[:2], ["", "Discord"])
        finally:
            notify.notifications, notify._run, notify._remove_async = old

    def test_explains_setup(self):
        if sys.platform != "win32":
            r = notify.PLUGIN.api("test", {}, Context(None))
            self.assertFalse(r["ok"])
            self.assertIn("Windows", r["message"])
        with self.assertRaises(notify.NotifyError) as e:
            notify.PLUGIN.widget("email", {}, Context(None, settings={}))
        self.assertIn("email server", str(e.exception))

    def test_email_unread_count(self):
        calls = []

        class FakeIMAP:
            class error(Exception):
                pass

            def __init__(self, host, port, timeout=None):
                calls.append(("connect", host, port))

            def login(self, u, p):
                if p != "app-pass":
                    raise notify.imaplib.IMAP4.error("bad")

            def status(self, folder, what):
                calls.append(("status", folder, what))
                return "OK", [b'"INBOX" (UNSEEN 7)']

            def logout(self):
                pass
        old = notify.imaplib.IMAP4_SSL
        notify.imaplib.IMAP4_SSL = FakeIMAP
        notify._cache.clear()
        try:
            s = {"imap_host": "imap.example.com", "imap_user": "me@example.com", "imap_password": "app-pass"}
            w = notify.PLUGIN.widget("email", {}, Context(None, settings=s))
            self.assertEqual((w["text"], w["on"]), ("7", True))
            self.assertEqual(calls[0], ("connect", "imap.example.com", 993))
            notify._cache.clear()
            with self.assertRaises(notify.NotifyError) as e:
                notify.unread({**s, "imap_password": "wrong"})
            self.assertIn("app password", str(e.exception))
        finally:
            notify.imaplib.IMAP4_SSL = old


class TestSubWaveQueue(unittest.TestCase):
    def setUp(self):
        self.log = []
        state = {"upcoming": [{"track": {"title": "01 - First", "artist": "A", "subsonic_id": "s1"}},
                              {"title": "Second", "artist": "B", "id": "s2"}]}
        admin = "Basic " + __import__("base64").b64encode(b"admin:pw").decode()

        def need_admin(obj):
            return lambda q, b, h: (200, obj) if h.get("Authorization") == admin else (401, {})
        routes = {("GET", "/api/state"): (200, state),
                  ("GET", "/api/now-playing"): (200, {"listeners": 3}),
                  ("DELETE", "/api/dj/queue/s2"): need_admin({"removed": True}),
                  ("GET", "/api/stats"): need_admin({"requests": {"total": 9, "resolved": 8}})}
        self.srv, url = serve(routes, self.log)
        self.ctx = Context(None, settings={"url": url, "username": "admin", "password": "pw"})
        subwave._forget()
        subwave._peak.clear()

    def tearDown(self):
        self.srv.shutdown()

    def test_up_next(self):
        P = subwave.PLUGIN
        self.assertEqual(P.widget("next", {"position": "1"}, self.ctx), {"icon": "⏭", "text": "First", "label": "A"})
        self.assertEqual(P.widget("next", {"position": "2"}, self.ctx)["text"], "Second")
        self.assertEqual(P.widget("next", {"position": "3"}, self.ctx)["on"], False)
        P.run("remove_next", {"position": "2"}, self.ctx)
        self.assertEqual(self.log[-1][:2], ("DELETE", "/api/dj/queue/s2"))
        with self.assertRaises(subwave.SubWaveError):
            P.run("remove_next", {"position": "5"}, self.ctx)

    def test_peak_and_requests(self):
        P = subwave.PLUGIN
        self.assertEqual(P.widget("peak", {}, self.ctx)["text"], "3")
        subwave._peak["n"] = 10
        self.assertEqual(P.widget("peak", {}, self.ctx)["text"], "10", "keeps the day's highest")
        subwave._peak["day"] = "1999-01-01"
        subwave._forget()
        self.assertEqual(P.widget("peak", {}, self.ctx)["text"], "3", "a new day starts over")
        self.assertEqual(P.widget("requests", {}, self.ctx)["text"], "8/9")


class TestWavelengthHandoff(unittest.TestCase):
    def setUp(self):
        self.log = []
        players = {"players": [{"id": 7, "name": "Desktop", "state": "idle"},
                               {"id": 9, "name": "Phone", "state": "playing", "title": "Song"}]}

        def handoff(q, body, h):
            return (200, {"ok": True, "from": "Phone", "to": "Desktop"}) if body == {"to": "Desktop"} else (404, {"error": "The player to hand off to isn't online"})
        routes = {("GET", "/api/ha/players"): (200, players), ("POST", "/api/ha/players/9/handoff"): handoff}
        self.srv, self.url = serve(routes, self.log)
        self.ctx = Context(None, settings={"url": self.url, "token": "wlh_x"})

    def tearDown(self):
        self.srv.shutdown()

    def test_handoff_from_whatever_is_playing(self):
        wavelength.PLUGIN.run("handoff", {"to": "Desktop"}, self.ctx)
        self.assertEqual(self.log[-1][:2], ("POST", "/api/ha/players/9/handoff"))
        with self.assertRaises(wavelength.WavelengthError) as e:
            wavelength.PLUGIN.run("handoff", {"to": "Kitchen"}, self.ctx)
        self.assertIn("isn't online", str(e.exception))
        with self.assertRaises(wavelength.WavelengthError):
            wavelength.PLUGIN.run("handoff", {"to": "phone"}, self.ctx)
        self.assertEqual([o["value"] for o in wavelength.PLUGIN.api("targets", {}, self.ctx)], ["Desktop", "Phone"])

    def test_old_hub_is_explained(self):
        srv, url = serve({("GET", "/api/ha/players"): (200, {"players": [{"id": 9, "name": "Phone", "state": "playing"}]}),
                          ("POST", "/api/ha/players/9/handoff"): (404, {"message": "Route POST:/api/ha/players/9/handoff not found"})}, [])
        try:
            with self.assertRaises(wavelength.WavelengthError) as e:
                wavelength.PLUGIN.run("handoff", {"to": "Desktop"}, Context(None, settings={"url": url, "token": "wlh_x"}))
            self.assertIn("Update Wavelength Hub", str(e.exception))
        finally:
            srv.shutdown()


class TestHAAlert(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.state = {"entity_id": "binary_sensor.doorbell", "state": "off", "last_changed": "2026-01-01T00:00:00+00:00"}
        self.srv, url = serve({("GET", "/api/states/binary_sensor.doorbell"): lambda q, b, h: (200, self.state)}, self.log)
        self.ctx = Context(None, settings={"url": url, "token": "t"})
        ha._ALERT_CLEARED.clear()
        self.started, ha._STARTED = ha._STARTED, time.time() - 60

    def tearDown(self):
        ha._STARTED = self.started
        self.srv.shutdown()

    def test_lights_until_cleared(self):
        P = ha.PLUGIN
        params = {"entity": "binary_sensor.doorbell", "state": "on", "name": "Doorbell"}
        self.assertEqual(P.widget("alert", params, self.ctx)["on"], False)
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        self.state.update(state="on", last_changed=now)
        w = P.widget("alert", params, self.ctx)
        self.assertEqual((w["on"], w["text"], w["label"]), (True, "now", "Doorbell"))
        P.run("clear_alert", {"entity": "binary_sensor.doorbell"}, self.ctx)
        self.assertEqual(P.widget("alert", params, self.ctx)["on"], False)

    def test_old_state_from_before_start_doesnt_alert(self):
        self.state.update(state="on")   # on since 2026-01-01, before VSD Pad started
        self.assertEqual(ha.PLUGIN.widget("alert", {"entity": "binary_sensor.doorbell"}, self.ctx)["on"], False)

    def test_pushed_change_is_caught_even_if_it_already_turned_off(self):
        class Link:
            entered = {("binary_sensor.doorbell", "on"): time.time() - 125}

            def get(self, ent):
                return {"state": "off", "last_changed": "2026-01-01T00:00:00+00:00"}
        old = ha._ha_live.link
        ha._ha_live.link = lambda *a, **k: Link()
        ha._STARTED = time.time() - 3600

        class App:
            live = object()
        try:
            w = ha.PLUGIN.widget("alert", {"entity": "binary_sensor.doorbell", "state": "on"},
                                 Context(App(), settings={"url": "http://x", "token": "t"}))
            self.assertEqual((w["on"], w["text"]), (True, "2m ago"))
        finally:
            ha._ha_live.link = old


if __name__ == "__main__":
    unittest.main(verbosity=1)
