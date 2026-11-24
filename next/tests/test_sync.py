"""Server + PC sync over real HTTP (no hardware).  Run:  python tests/test_sync.py

One process plays both sides: a server App (no pads, password, sync hub) and a PC App with a simulated pad that
follows it.
"""
import base64, http.client, io, json, os, shutil, sys, tempfile, threading, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from vsdpad import migrate, sync  # noqa: E402
from vsdpad.app import App  # noqa: E402
from vsdpad.devices import fake  # noqa: E402
from vsdpad.server import SettingsServer  # noqa: E402

PW = "correct horse"


def write_cfg(d, cfg):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def wait(cond, t=5):
    end = time.time() + t
    while time.time() < end and not cond():
        time.sleep(0.03)
    return cond()


class TestSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vsdpad-sync-")
        # the server: starter layout with a Home Assistant token, no pads
        cfg = migrate.default_config()
        cfg["devices"] = {}
        cfg["plugins"]["homeassistant"] = {"url": "http://ha.local", "token": "TOPSECRET"}
        write_cfg(os.path.join(cls.tmp, "server"), cfg)
        cls.server_app = App(os.path.join(cls.tmp, "server"), fake_devices=[])
        cls.server_app.hub = sync.Hub(cls.server_app)
        cls.server_app.start()
        cls.srv = SettingsServer(cls.server_app, port=18781, host="127.0.0.2", password=PW)
        cls.srv.start()
        # the PC: its own (different) layout and a simulated M18
        pc = migrate.default_config()
        pc["brightness"] = 33
        write_cfg(os.path.join(cls.tmp, "pc"), pc)
        cls.pad = fake.FakeDevice(fake.info_for(id="m18", model="VSD M18"))
        cls.pc_app = App(os.path.join(cls.tmp, "pc"), fake_devices=[cls.pad])
        cls.pc_srv = SettingsServer(cls.pc_app, port=18782)
        cls.pc_srv.start()
        cls.url = "http://127.0.0.2:18781"

    @classmethod
    def tearDownClass(cls):
        if cls.pc_app.follower:
            cls.pc_app.follower.stop()
        for s in (cls.srv, cls.pc_srv):
            s.stop()
        for a in (cls.server_app, cls.pc_app):
            a.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, password=PW, headers=None, pc=False):
        """A request to the server, or with pc=True to the PC's own settings page."""
        port = self.pc_srv.port if pc else self.srv.port
        c = http.client.HTTPConnection("127.0.0.1" if pc else "127.0.0.2", port, timeout=40)
        h = {"Host": f"127.0.0.1:{port}", "X-VSDPad-Token": self.pc_srv.token} if pc else {"Host": f"tower.local:{port}"}
        if password is not None:
            h.update(sync.auth_headers(password))
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body)
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        ctype = r.getheader("Content-Type", "")
        return r.status, (json.loads(data) if ctype.startswith("application/json") and data else data), r

    def follower(self):
        if not self.pc_app.follower:
            self.pc_app.follower = sync.Follower(self.pc_app, sync.Client(self.url, PW), self.pc_srv)
        return self.pc_app.follower

    # ---- the server's door ----
    def test_1_password(self):
        st, _, r = self.req("GET", "/", password=None)
        self.assertEqual(st, 401)
        self.assertIn("Basic", r.getheader("WWW-Authenticate"))
        self.assertEqual(self.req("GET", "/sync/hello", password="wrong")[0], 401)
        self.assertEqual(self.req("GET", "/healthz", password=None)[0], 200)
        st, page, _ = self.req("GET", "/")   # any Host name works with the password
        self.assertEqual(st, 200)
        self.assertIn(self.srv.token.encode(), page)
        self.assertEqual(self.req("GET", "/api/state")[0], 401)   # still needs the page token too
        st, s, _ = self.req("GET", "/api/state", headers={"X-VSDPad-Token": self.srv.token})
        self.assertEqual((st, s["mode"]), (200, "server"))
        self.assertNotIn("TOPSECRET", json.dumps(s))
        self.assertEqual(self.req("POST", "/api/config", {}, headers={"X-VSDPad-Token": self.srv.token, "Origin": "http://evil.com"})[0], 403)
        with self.assertRaises(ValueError):
            SettingsServer(self.server_app, host="0.0.0.0")   # no password, no network server
        self.assertEqual(self.req("GET", "/sync/hello", pc=True, password=None)[0], 404)   # a PC has no /sync

    def test_2_paths(self):
        base = os.path.join(self.tmp, "x")
        self.assertIsNone(sync.safe_path(base, "../config.json"))
        self.assertIsNone(sync.safe_path(base, "a/../../b"))
        self.assertIsNone(sync.safe_path(base, "/etc/passwd"))
        self.assertIsNone(sync.safe_path(base, ".hidden"))
        self.assertIsNone(sync.safe_path(base, "C:\\x"))
        self.assertIsNone(sync.safe_path(base, "cyberpunk/run.py"))
        self.assertTrue(sync.safe_path(base, "cyberpunk/pack.json").endswith(os.path.join("x", "cyberpunk", "pack.json")))
        self.assertEqual(self.req("GET", "/sync/file?root=images&path=../config.json")[0], 404)
        self.assertEqual(self.req("GET", "/sync/file?root=nope&path=a.png")[0], 404)
        self.assertEqual(self.req("POST", "/sync/file", {"root": "images", "path": "../../evil.py", "data": ""})[0], 400)
        self.assertEqual(sync.normalize_url("tower"), "http://tower:8765")
        self.assertEqual(sync.normalize_url("http://10.0.0.5:9000/"), "http://10.0.0.5:9000")
        d = os.path.join(self.tmp, "linktest")
        self.assertIsNone(sync.load_link(d))
        sync.save_link(d, "http://tower:8765", "pw")
        self.assertEqual(sync.load_link(d), ("http://tower:8765", "pw"))
        self.assertTrue(sync.remove_link(d))

    # ---- following ----
    def test_3_follow(self):
        # a picture on the server, used by a key
        b = io.BytesIO()
        Image.new("RGB", (64, 64), (10, 200, 30)).save(b, "PNG")
        name = "ab" * 12 + ".png"
        with open(os.path.join(self.server_app.images_dir, name), "wb") as f:
            f.write(b.getvalue())
        self.server_app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"].__setitem__("6", {"label": "Pic", "image": "img:" + name}))
        f = self.follower()
        f.poll_once(wait=0)
        cfg = self.pc_app.store.get()
        self.assertEqual(cfg["brightness"], 80)                             # the server's layout, not the PC's 33
        self.assertEqual(cfg["plugins"]["homeassistant"]["token"], "TOPSECRET")   # the PC needs the token to run keys
        self.assertEqual(cfg["layouts"][0]["pages"][0]["keys"]["6"]["image"], "img:" + name)
        self.assertTrue(os.path.exists(os.path.join(self.pc_app.images_dir, name)))
        self.assertTrue(self.pc_app.store.history())                        # the PC's own layout was kept in its history

        # nothing changed: the server holds the poll; a Save answers it at once
        done = {}
        t = threading.Thread(target=lambda: done.setdefault("r", f.poll_once(wait=20)))
        t0 = time.time()
        t.start()
        time.sleep(0.5)
        self.assertNotIn("r", done)
        self.server_app.store.update(lambda c: c.__setitem__("brightness", 55))
        t.join(10)
        self.assertLess(time.time() - t0, 5)
        self.assertIn("config", done["r"])
        self.assertEqual(self.pc_app.store.get()["brightness"], 55)

        # the PC's settings page sends you to the server instead of saving
        st, r, _ = self.req("POST", "/api/config", {"config": cfg}, pc=True)
        self.assertEqual(st, 409)
        self.assertIn(self.url, r["error"])
        st, s, _ = self.req("GET", "/api/state", pc=True)
        self.assertEqual((s["mode"], s["managed_by"]), ("follower", self.url))

    def test_4_pads_and_presses(self):
        f = self.follower()
        f.poll_once(wait=0)
        self.pc_app.start()
        self.assertTrue(wait(lambda: self.pc_app.runner("m18") and self.pc_app.runner("m18").connected))
        threading.Thread(target=f._status_loop, daemon=True).start()
        # the server shows the PC's pad
        self.assertTrue(wait(lambda: any(d["id"] == "m18" for d in self.server_app.status()["devices"])))
        st = self.server_app.status()
        self.assertTrue(st["server"])
        self.assertEqual(st["agents"][0]["name"], f.name)
        self.assertNotIn("No PC is connected", " ".join(st["notes"]))
        self.assertIn("m18", self.server_app.store.get()["devices"])          # and gave it pages

        # going to a page in the server's settings page moves the pad on the PC
        self.server_app.store.update(lambda c: c["layouts"][0]["pages"].append({"id": "psync2", "name": "Two", "folder": False, "keys": {}}))
        f.poll_once(wait=0)
        st, r, _ = self.req("POST", "/api/goto", {"device": "m18", "layout": "main", "page": "psync2"},
                            headers={"X-VSDPad-Token": self.srv.token})
        self.assertEqual(st, 200, r)
        f.poll_once(wait=0)
        self.assertTrue(wait(lambda: self.pc_app.runner("m18").nav.current_id == "psync2"))
        st, r, _ = self.req("POST", "/api/run", {"device": "m18", "layout": "main",
                                                 "steps": [{"plugin": "pages", "action": "home", "params": {}}]},
                            headers={"X-VSDPad-Token": self.srv.token})
        self.assertEqual((st, r.get("sent")), (200, True), r)
        f.poll_once(wait=0)
        self.assertTrue(wait(lambda: self.pc_app.runner("m18").nav.current_id == "pmain01"))

    def test_5_upload_layout(self):
        f = self.follower()
        f.poll_once(wait=0)
        b = io.BytesIO()
        Image.new("RGB", (32, 32), (250, 0, 0)).save(b, "PNG")
        name = "cd" * 12 + ".png"
        with open(os.path.join(self.pc_app.images_dir, name), "wb") as fh:
            fh.write(b.getvalue())
        cfg = self.pc_app.store.get()
        cfg["brightness"] = 21
        cfg["layouts"][0]["pages"][0]["keys"]["7"] = {"label": "Mine", "image": "img:" + name}
        cfg["plugins"]["homeassistant"] = {"url": "http://ha.local"}   # no token: the server keeps its own
        self.pc_app.store.replace(cfg)
        n = f.upload_layout()
        self.assertGreaterEqual(n, 1)
        s = self.server_app.store.get()
        self.assertEqual(s["brightness"], 21)
        self.assertEqual(s["layouts"][0]["pages"][0]["keys"]["7"]["label"], "Mine")
        self.assertEqual(s["plugins"]["homeassistant"]["token"], "TOPSECRET")
        self.assertTrue(os.path.exists(os.path.join(self.server_app.images_dir, name)))

    # ---- updates for the PC, handed on from GitHub ----
    def test_6_updates(self):
        import hashlib, urllib.error, urllib.request
        from unittest import mock
        from vsdpad import updater
        from vsdpad.update_mirror import UpdateMirror
        blob = b"MZ pretend installer" * 500
        name = "VSDPad-Setup-2.0.0-dev-build12.exe"
        gh_calls = []

        def github(req, timeout=None):
            gh_calls.append(req.full_url)
            if req.full_url == updater.LATEST:
                body = {"tag_name": "build-12", "body": "notes", "assets": [
                    {"name": name, "url": "https://api.github.com/assets/12", "size": len(blob),
                     "digest": "sha256:" + hashlib.sha256(blob).hexdigest()}]}
                return io.BytesIO(json.dumps(body).encode())
            return io.BytesIO(blob)

        self.srv._mirror = UpdateMirror(self.server_app.data_dir, "github_pat_server", github)
        self.assertEqual(self.req("GET", "/sync/updates/latest", password="wrong")[0], 401)
        self.assertEqual(self.req("GET", "/sync/updates/latest", pc=True, password=None)[0], 404)   # not on a PC
        st, rel, _ = self.req("GET", "/sync/updates/latest")
        self.assertEqual((st, rel["tag_name"]), (200, "build-12"))
        self.assertEqual(rel["assets"][0]["url"], "/sync/updates/asset/" + name)
        self.assertEqual(self.req("GET", "/sync/updates/asset/evil.exe")[0], 404)
        # the PC: linked to this server, no GitHub token of its own
        pc_store = mock.Mock()
        pc_store.plugin_settings.return_value = {}
        pc = mock.Mock(store=pc_store, data_dir=os.path.join(self.tmp, "pc"))
        folder = tempfile.mkdtemp(dir=self.tmp)
        with mock.patch.object(updater, "current_build", lambda: 10):
            u = updater.Updater(pc, None, urllib.request.build_opener(urllib.request.ProxyHandler({})).open,
                                folder=folder)
        with mock.patch.object(u, "_link", lambda: (self.url, PW)):
            st = u.check()
            self.assertEqual((st["source"], st["latest"], st["available"]), ("server", 12, True))
            path = u.download(u.state["asset"])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), blob)
        self.assertEqual(sum(1 for c in gh_calls if "assets" in c), 1)   # GitHub asked once


if __name__ == "__main__":
    unittest.main(verbosity=1)
