"""Profiles, the phone remote and sharing packs (simulated pad, no hardware).  Run:  python tests/test_profiles_remote.py"""
import base64, datetime as dt, http.client, io, json, os, shutil, sys, tempfile, time, unittest, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from vsdpad import layoutprofiles as lp, migrate, sharepack  # noqa: E402
from vsdpad.app import App  # noqa: E402
from vsdpad.devices import fake  # noqa: E402
from vsdpad.schema import ConfigError, validate  # noqa: E402
from vsdpad.server import SettingsServer  # noqa: E402


def key(label, **kw):
    return {"label": label, "icon": "★", **kw}


def base_config():
    cfg = migrate.default_config()
    cfg["layouts"][0]["pages"][0]["keys"] = {"1": key("Work mail")}
    return cfg


class TestProfileLogic(unittest.TestCase):
    def test_add_switch_and_back(self):
        cfg = base_config()
        cfg["devices"] = {"m18": {"name": "M18", "layout": cfg["layouts"][0]["id"]}}
        gid = lp.add(cfg, "Gaming", "empty")
        validate(cfg)
        self.assertEqual([p["name"] for p in lp.summary(cfg)], ["Main", "Gaming"])
        self.assertEqual(lp.switch(cfg, "gaming"), "Gaming")       # by name, any case
        validate(cfg)
        self.assertEqual(lp.active(cfg)["id"], gid)
        self.assertEqual(cfg["layouts"][0]["pages"][0]["keys"], {})  # empty pages, same pad size
        cfg["layouts"][0]["pages"][0]["keys"]["3"] = key("Discord")
        lp.switch(cfg, "Main")
        validate(cfg)
        self.assertEqual(cfg["layouts"][0]["pages"][0]["keys"]["1"]["label"], "Work mail")
        lp.switch(cfg, gid)
        self.assertEqual(cfg["layouts"][0]["pages"][0]["keys"]["3"]["label"], "Discord")  # edits were kept

    def test_copy_and_step(self):
        cfg = base_config()
        lp.add(cfg, "Streaming")
        lp.add(cfg, "Streaming")      # a second one with the same name gets "(2)"
        names = [p["name"] for p in lp.summary(cfg)]
        self.assertEqual(names, ["Main", "Streaming", "Streaming (2)"])
        self.assertEqual(lp.step(cfg, 1), "Streaming")
        self.assertEqual(cfg["layouts"][0]["pages"][0]["keys"]["1"]["label"], "Work mail")  # a copy
        self.assertEqual(lp.step(cfg, -1), "Main")
        self.assertEqual(lp.step(cfg, -1), "Streaming (2)")
        validate(cfg)

    def test_app_rules_and_theme_go_with_the_profile(self):
        cfg = base_config()
        page = cfg["layouts"][0]["pages"][0]["id"]
        cfg["profiles"] = {"enabled": True, "rules": [{"app": "obs64.exe", "page": page}]}
        cfg["theme"] = "cyberpunk"
        lp.add(cfg, "Gaming", "empty")
        lp.switch(cfg, "Gaming")
        validate(cfg)   # the rule pointed at a page Gaming doesn't have: it stayed with Main
        self.assertEqual(cfg["profiles"]["rules"], [])
        self.assertEqual(cfg["theme"], "cyberpunk")   # an empty profile starts with the same look
        cfg["theme"] = "nuclear"
        lp.switch(cfg, "Main")
        self.assertEqual(cfg["theme"], "cyberpunk")
        self.assertEqual(cfg["profiles"]["rules"][0]["page"], page)

    def test_pad_of_another_size_gets_pages(self):
        cfg = base_config()
        cfg["devices"] = {"m18": {"name": "M18", "layout": "main"}}
        lp.add(cfg, "Shared", "data", saved={"layouts": [{"id": "xl", "name": "XL", "cols": 8, "rows": 4, "buttons": {},
                                                          "pages": [{"id": "pa1", "name": "One", "keys": {}}]}]})
        validate(cfg)
        lp.switch(cfg, "Shared")
        validate(cfg)
        dev_lay = next(l for l in cfg["layouts"] if l["id"] == cfg["devices"]["m18"]["layout"])
        self.assertEqual((dev_lay["cols"], dev_lay["rows"]), (5, 3))   # the M18 got new empty 5x3 pages
        lp.switch(cfg, "Main")
        self.assertEqual(cfg["devices"]["m18"]["layout"], "main")

    def test_validation(self):
        cfg = base_config()
        lp.add(cfg, "Gaming", "empty")
        bad = json.loads(json.dumps(cfg))
        bad["layout_profiles"]["active"] = "nope"
        self.assertRaises(ConfigError, validate, bad)
        bad = json.loads(json.dumps(cfg))
        bad["layout_profiles"]["list"][1]["saved"]["layouts"][0]["pages"][0]["keys"] = {"99": {}}
        with self.assertRaises(ConfigError) as e:
            validate(bad)
        self.assertIn("Gaming", str(e.exception))
        bad = json.loads(json.dumps(cfg))
        bad["layout_profiles"]["schedule"] = [{"profile": "gaming", "at": "25:00"}]
        self.assertRaises(ConfigError, validate, bad)
        self.assertRaises(ValueError, lp.delete, cfg, "Main")   # in use
        lp.delete(cfg, "Gaming")
        validate(cfg)

    def test_schedule(self):
        tz = dt.timezone.utc
        sched = [{"profile": "work", "at": "09:00", "days": [0, 1, 2, 3, 4]}, {"profile": "game", "at": "18:00"}]
        mon = dt.datetime(2026, 11, 9, tzinfo=tz)   # a Monday
        at = lambda d, h, m=0: mon + dt.timedelta(days=d, hours=h, minutes=m)  # noqa: E731
        self.assertEqual(lp.due(sched, at(0, 8, 59), at(0, 9, 0))["profile"], "work")
        self.assertIsNone(lp.due(sched, at(0, 9, 0), at(0, 9, 1)))                 # only once
        self.assertIsNone(lp.due(sched, at(5, 8, 59), at(5, 9, 1)))                # not on Saturday
        self.assertEqual(lp.due(sched, at(0, 8), at(0, 19))["profile"], "game")    # asleep through both: the latest
        self.assertEqual(lp.due(sched, at(0, 23), at(1, 9, 30))["profile"], "work")  # over midnight


class TestRunningApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="vsdpad-prof-")
        with open(os.path.join(cls.d, "config.json"), "w", encoding="utf-8") as f:
            json.dump(base_config(), f)
        cls.pad = fake.FakeDevice(fake.info_for(id="m18"))
        cls.app = App(cls.d, fake_devices=[cls.pad])
        cls.app.start()
        cls.srv = SettingsServer(cls.app, port=18865)
        cls.srv.start()
        deadline = time.time() + 5
        while not cls.app.runners() or not cls.app.runners()[0].connected:
            time.sleep(0.05)
            if time.time() > deadline:
                raise RuntimeError("simulated pad didn't connect")

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.app.stop()
        shutil.rmtree(cls.d, ignore_errors=True)

    def api(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.srv.port}", "X-VSDPad-Token": self.srv.token}
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)

    def remote_call(self, method, path, body=None, token=None, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.app.remote.port, timeout=10)
        h = {"X-Remote-Token": token or ""}
        if body is not None:
            h["Content-Type"] = ctype
            body = json.dumps(body)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)

    def wait_for(self, cond, what):
        deadline = time.time() + 5
        while not cond():
            if time.time() > deadline:
                self.fail(what)
            time.sleep(0.05)

    def test_1_profiles_from_settings_and_key(self):
        st, r = self.api("POST", "/api/profile", {"do": "add", "name": "Gaming", "start": "empty"})
        self.assertEqual(st, 200, r)
        st, r = self.api("POST", "/api/profile", {"do": "switch", "profile": "Gaming"})
        self.assertEqual((st, r["name"]), (200, "Gaming"))
        self.assertEqual(self.api("GET", "/api/status")[1]["profile"]["name"], "Gaming")
        # a key on the pad that switches back
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"].update(
            {"2": key("Work", tap=[{"plugin": "profiles", "action": "switch", "params": {"profile": "main"}}])}))
        self.wait_for(lambda: 2 in self.pad.images and self.app.runners()[0].seen and
                      self.app.runners()[0].seen[0] == self.app.store.version, "pad didn't redraw")
        self.pad.press(2)
        self.pad.press(2, False)
        self.wait_for(lambda: lp.active(self.app.store.get())["name"] == "Main", "key didn't switch profile")
        self.wait_for(lambda: self.app.runners()[0].page()["keys"].get("1", {}).get("label") == "Work mail", "pad didn't follow")
        # the scheduler switches when the time comes
        now = dt.datetime.now().astimezone()
        self.app.store.update(lambda c: c["layout_profiles"].__setitem__("schedule", [{"profile": "gaming", "at": (now + dt.timedelta(minutes=1)).strftime("%H:%M")}]))
        sch = lp.Scheduler(self.app, clock=lambda: now)
        sch.clock = lambda: now + dt.timedelta(minutes=2)
        self.assertEqual(sch.tick(), "Gaming")
        lp.switch_now(self.app, "main")
        st, r = self.api("POST", "/api/profile", {"do": "delete", "profile": "main"})
        self.assertEqual(st, 400)   # in use

    def test_2_remote_pairing_and_pressing(self):
        rm = self.app.remote
        rm.state["port"] = 18890
        self.assertFalse(rm.running)
        st, info = self.api("POST", "/api/remote", {"do": "on"})
        self.assertTrue(info["running"], info)
        self.assertEqual(self.remote_call("GET", "/r/state")[0], 401)
        self.assertEqual(self.remote_call("POST", "/r/pair", {"code": "123456"})[0], 403)   # no code showing
        st, info = self.api("POST", "/api/remote", {"do": "pair"})
        code = info["pairing"]["code"]
        rm._last_try = 0
        st, r = self.remote_call("POST", "/r/pair", {"code": "000000" if code != "000000" else "111111"})
        self.assertEqual(st, 403)
        rm._last_try = 0
        self.assertEqual(self.remote_call("POST", "/r/pair", {"code": code}, ctype="text/plain")[0], 415)
        st, r = self.remote_call("POST", "/r/pair", {"code": code, "name": "Test phone"})
        self.assertEqual(st, 200, r)
        tok = r["token"]
        with open(os.path.join(self.d, "remote.json"), encoding="utf-8") as f:
            self.assertNotIn(tok, f.read())                      # only a fingerprint is saved
        rm._last_try = 0
        self.assertEqual(self.remote_call("POST", "/r/pair", {"code": code})[0], 403)   # a code works once
        st, s = self.remote_call("GET", "/r/state", token=tok)
        self.assertEqual(st, 200, s)
        self.assertEqual(s["pad"]["id"], "m18")
        self.assertEqual((s["pad"]["cols"], s["pad"]["rows"]), (5, 3))
        self.assertTrue(s["pad"]["keys"][0])                     # key 1 has a picture
        st, png = self.remote_call("GET", "/r/key?pad=m18&slot=1", token=tok)
        self.assertEqual(Image.open(io.BytesIO(png)).size, (216, 216))
        # pressing a key from the phone runs it, like on the pad
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"].update(
            {"5": key("Next", tap=[{"plugin": "pages", "action": "next", "params": {}}])}))
        self.app.store.update(lambda c: c["layouts"][0]["pages"].append({"id": "psecond", "name": "Two", "folder": False, "keys": {}}))
        r = self.app.runners()[0]
        self.wait_for(lambda: len(r.nav.ids) == 2, "pad didn't see page two")
        self.assertEqual(self.remote_call("POST", "/r/press", {"pad": "m18", "key": 5, "down": True}, token=tok)[0], 200)
        self.remote_call("POST", "/r/press", {"pad": "m18", "key": 5, "down": False}, token=tok)
        self.wait_for(lambda: r.nav.current_id == "psecond", "phone press didn't run the key")
        st, _ = self.remote_call("POST", "/r/page", {"pad": "m18", "page": r.nav.ids[0]}, token=tok)
        self.wait_for(lambda: r.nav.current == 0, "phone page change didn't work")
        self.assertEqual(self.remote_call("POST", "/r/press", {"pad": "m18", "key": 99}, token=tok)[0], 400)
        # removing the phone stops it at once; switching off stops the server
        phone_id = self.api("GET", "/api/remote")[1]["phones"][0]["id"]
        self.api("POST", "/api/remote", {"do": "remove", "id": phone_id})
        self.assertEqual(self.remote_call("GET", "/r/state", token=tok)[0], 401)
        self.assertFalse(self.api("POST", "/api/remote", {"do": "off"})[1]["running"])

    def test_3_sharing_pack_round_trip(self):
        # a key with a picture and an icon pack of our own
        im = Image.new("RGB", (64, 64), (200, 30, 30))
        b = io.BytesIO()
        im.save(b, "PNG")
        st, up = self.api("POST", "/api/upload", {"data": base64.b64encode(b.getvalue()).decode()})
        icons = io.BytesIO()
        with zipfile.ZipFile(icons, "w") as z:
            ib = io.BytesIO()
            Image.new("RGBA", (96, 96), (255, 255, 255, 255)).save(ib, "PNG")
            z.writestr("star.png", ib.getvalue())
        st, ip = self.api("POST", "/api/iconpack_import", {"data": base64.b64encode(icons.getvalue()).decode(), "name": "Pack test"})
        self.assertEqual(st, 200, ip)
        pid = ip["id"]
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"].update(
            {"7": key("Pic", image=up["ref"]), "8": {"icon": f"{pid}:star", "label": "Star"}}))
        st, ch = self.api("GET", "/api/pack_choices")
        cur = next(p for p in ch["profiles"] if p["active"])
        self.assertEqual(cur["images"], [up["ref"][4:]])
        self.assertEqual(cur["iconpacks"], [pid])
        st, blob = self.api("GET", "/api/pack_export?" + "t=x&name=My%20desk")
        self.assertEqual(st, 200, blob)
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = set(z.namelist())
        self.assertIn("profile.json", names)
        self.assertIn("images/" + up["ref"][4:], names)
        self.assertIn(f"iconpacks/{pid}.zip", names)
        self.assertNotIn("plugins", json.loads(z.read("profile.json")))
        st, info = self.api("POST", "/api/pack_inspect", {"data": base64.b64encode(blob).decode()})
        self.assertEqual(info["profile"]["name"], "Main")
        self.assertEqual(info["iconpacks"], [{"id": pid, "have": True}])
        # importing adds a new profile and leaves everything else alone
        os.remove(os.path.join(self.app.images_dir, up["ref"][4:]))
        st, done = self.api("POST", "/api/pack_import", {"data": base64.b64encode(blob).decode(), "use_now": True})
        self.assertEqual(st, 200, done)
        self.assertEqual(done["images"], 1)
        self.assertEqual(lp.active(self.app.store.get())["name"], done["profile"])
        self.assertTrue(os.path.exists(os.path.join(self.app.images_dir, up["ref"][4:])))
        self.assertEqual(self.app.store.get()["layouts"][0]["pages"][0]["keys"]["8"]["icon"], f"{pid}:star")
        lp.switch_now(self.app, "main")
        # an old layout backup (.json) imports as a profile too
        exp = json.dumps(self.app.store.export()).encode()
        self.assertEqual(sharepack.inspect(self.app, exp)["profile"]["name"], "Imported layout")
        # junk is refused with a readable message
        for junk in (b"hello", b"PK\x03\x04broken"):
            st, r = self.api("POST", "/api/pack_inspect", {"data": base64.b64encode(junk).decode()})
            self.assertEqual(st, 400)
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as zz:
            zz.writestr("manifest.json", json.dumps({"vsdpad_pack": 1}))
            zz.writestr("images/" + "a" * 24 + ".png", b"not a picture")
            zz.writestr("../../evil.txt", b"x")
        st, r = self.api("POST", "/api/pack_import", {"data": base64.b64encode(bad.getvalue()).decode()})
        self.assertEqual(st, 400)
        self.assertIn("damaged", r["error"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
