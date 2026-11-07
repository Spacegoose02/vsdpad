"""Settings server tests over real HTTP (simulated pad, no hardware).  Run:  python tests/test_server.py"""
import base64, http.client, io, json, os, shutil, sys, tempfile, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from vsdpad import migrate  # noqa: E402
from vsdpad.app import App  # noqa: E402
from vsdpad.devices import fake  # noqa: E402
from vsdpad.server import SettingsServer  # noqa: E402


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="vsdpad-srv-")
        cfg = migrate.default_config()
        cfg["plugins"]["homeassistant"] = {"url": "http://ha.local", "token": "TOPSECRET"}
        with open(os.path.join(cls.d, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        cls.pad = fake.FakeDevice(fake.info_for(id="m18"))
        cls.app = App(cls.d, fake_devices=[cls.pad])
        cls.app.start()
        cls.srv = SettingsServer(cls.app, port=18765)
        cls.srv.start()
        cls.port = cls.srv.port
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.app.stop()
        shutil.rmtree(cls.d, ignore_errors=True)

    def req(self, method, path, body=None, token=True, host=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            h["X-VSDPad-Token"] = self.srv.token
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body)
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") and data else data)

    def test_page_has_token(self):
        st, page = self.req("GET", "/", token=False)
        self.assertEqual(st, 200)
        self.assertIn(self.srv.token.encode(), page)

    def test_guards(self):
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/state", host="evil.com")[0], 403)
        self.assertEqual(self.req("POST", "/api/config", {}, headers={"Origin": "http://evil.com"})[0], 403)
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("POST", "/api/config", body="x", headers={"X-VSDPad-Token": self.srv.token, "Content-Type": "text/plain"})
        self.assertEqual(c.getresponse().status, 415)

    def test_state_has_no_secrets(self):
        st, s = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertNotIn("TOPSECRET", json.dumps(s))
        self.assertEqual(s["secret_set"], {"homeassistant": ["token"]})
        self.assertEqual(s["status"]["devices"][0]["id"], "m18")

    def test_save_keeps_secrets_and_validates(self):
        _, s = self.req("GET", "/api/state")
        cfg = s["config"]
        before = cfg["brightness"]
        cfg["brightness"] = 42
        cfg.pop("plugins")
        st, _ = self.req("POST", "/api/config", {"config": cfg})
        self.assertEqual(st, 200)
        self.assertEqual(self.app.store.get()["brightness"], 42)
        self.assertEqual(self.app.store.get()["plugins"]["homeassistant"]["token"], "TOPSECRET")
        self.assertTrue(self.wait(lambda: self.pad.brightness == 42))
        cfg["layouts"][0]["pages"][0]["folder"] = True
        st, r = self.req("POST", "/api/config", {"config": cfg})
        self.assertEqual(st, 400)
        self.assertIn("first page", r["error"])
        st, _ = self.req("POST", "/api/undo", {})
        self.assertEqual(st, 200)
        self.assertEqual(self.app.store.get()["brightness"], before)

    def wait(self, cond, t=3):
        end = time.time() + t
        while time.time() < end and not cond():
            time.sleep(0.03)
        return cond()

    def test_upload_preview_and_image(self):
        b = io.BytesIO()
        Image.new("RGB", (800, 600), (200, 30, 30)).save(b, "JPEG")
        st, r = self.req("POST", "/api/upload", {"data": "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()})
        self.assertEqual(st, 200, r)
        ref = r["ref"]
        self.assertRegex(ref, r"^img:[0-9a-f]{24}\.png$")
        st, png = self.req("POST", "/api/preview", {"key": {"image": ref, "label": "Red"}, "slot": 1})
        self.assertEqual(st, 200)
        self.assertGreater(Image.open(io.BytesIO(png)).getpixel((72, 50))[0], 150)
        st, _ = self.req("GET", "/api/image?ref=" + ref)
        self.assertEqual(st, 200)
        self.assertEqual(self.req("POST", "/api/upload", {"data": base64.b64encode(b"nope").decode()})[0], 400)
        self.assertEqual(self.req("GET", "/api/image?ref=img:../config.json")[0], 404)

    def test_run_and_goto(self):
        cfg = self.app.store.get()
        self.app.store.update(lambda c: c["layouts"][0]["pages"].append({"id": "ptest1", "name": "T", "folder": False, "keys": {}}))
        st, r = self.req("POST", "/api/run", {"steps": [{"plugin": "pages", "action": "next", "params": {}}], "device": "m18"})
        self.assertEqual((st, r.get("page")), (200, 1), r)
        st, r = self.req("POST", "/api/run", {"steps": [{"plugin": "nope", "action": "x", "params": {}}], "device": "m18"})
        self.assertEqual(st, 400)
        self.assertEqual(self.req("POST", "/api/goto", {"device": "m18", "page": "pmain01"})[0], 200)
        self.assertEqual(self.app.runner("m18").nav.current_id, "pmain01")
        self.app.store.replace(cfg)

    def test_export_import(self):
        st, data = self.req("GET", "/api/export")
        self.assertEqual(st, 200)
        self.assertNotIn("TOPSECRET", json.dumps(data))
        data["config"]["brightness"] = 11
        self.assertEqual(self.req("POST", "/api/import", {"data": data})[0], 200)
        self.assertEqual(self.app.store.get()["brightness"], 11)
        self.assertEqual(self.app.store.get()["plugins"]["homeassistant"]["token"], "TOPSECRET")
        self.assertEqual(self.req("POST", "/api/import", {"data": {"x": 1}})[0], 400)

    def test_plugin_settings_secret_blank_keeps(self):
        self.req("POST", "/api/plugin/homeassistant/settings", {"settings": {"url": "http://new", "token": ""}})
        s = self.app.store.get()["plugins"]["homeassistant"]
        self.assertEqual((s["url"], s["token"]), ("http://new", "TOPSECRET"))

    def test_theme_preview(self):
        st, png = self.req("GET", "/api/theme_preview.png?theme=nuclear&layout=main&page=0")
        self.assertEqual(st, 200)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (588, 348))


if __name__ == "__main__":
    unittest.main(verbosity=1)
