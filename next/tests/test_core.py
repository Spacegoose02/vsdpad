"""Core tests: config, migration, history, navigation, gestures, drawing, live keys, app rules, devices.
Needs no hardware.  Run:  python tests/test_core.py
"""
import base64, io, json, os, shutil, sys, tempfile, threading, time, unittest, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from vsdpad import actions, migrate, profiles, render, schema, store, themes_io  # noqa: E402
from vsdpad.app import App  # noqa: E402
from vsdpad.devices import fake, m18, streamdeck  # noqa: E402
from vsdpad.navigator import Navigator  # noqa: E402
from vsdpad.plugins import clock, steam, timer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
V3 = {"version": 3, "brightness": 70, "icon_pack": "nuclear", "row_offset": [0, 0, 8], "round_buttons": [37, 48, 49],
      "round": {"37": {"label": "Prev", "action": {"plugin": "pages", "action": "previous", "params": {}}}},
      "pages": [{"name": "Main", "keys": {"1": {"icon": "♪", "label": "Vol", "color": [1, 2, 3], "repeat": True,
                                             "action": {"plugin": "system", "action": "media", "params": {"key": "vol_up"}}}}},
                {"name": "Two", "keys": {}}],
      "plugins": {"homeassistant": {"url": "http://ha.local:8123", "token": "SECRET"}}}
V1 = {"keys": {"1": {"label": "Play", "action": {"type": "media", "key": "play"}},
               "2": {"label": "Copy", "action": {"type": "hotkey", "keys": ["ctrl", "c"]}},
               "37": {"label": "Next", "action": {"type": "url", "url": "https://x"}}}}


def tmpdir(cfg=None):
    d = tempfile.mkdtemp(prefix="vsdpad-test-")
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg if cfg is not None else migrate.default_config(), f)
    return d


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class RecordingApp:
    """Minimal app for gesture tests: records which steps ran."""
    def __init__(self):
        self.ran, self.errors = [], []
        self.lock = threading.Lock()

    def run_action(self, step, device_id=None, nav=None):
        with self.lock:
            self.ran.append(step["action"])

    def report_error(self, d, m):
        self.errors.append(m)


# ------------------------------------------------------------------------------------------
class TestMigration(unittest.TestCase):
    def test_v3_to_v5(self):
        cfg, changed, ver = migrate.migrate(V3)
        self.assertTrue(changed)
        self.assertEqual(ver, 3)
        schema.validate(cfg)
        lay = cfg["layouts"][0]
        self.assertEqual((lay["cols"], lay["rows"]), (5, 3))
        self.assertEqual([p["name"] for p in lay["pages"]], ["Main", "Two"])
        k = lay["pages"][0]["keys"]["1"]
        self.assertEqual(k["tap"], [{"plugin": "system", "action": "media", "params": {"key": "vol_up"}}])
        self.assertTrue(k["repeat"])
        self.assertNotIn("action", k)
        self.assertEqual(lay["buttons"]["37"]["tap"][0]["action"], "previous")
        self.assertEqual(cfg["theme"], "nuclear")
        self.assertEqual(cfg["devices"]["m18"]["row_offset"], [0, 0, 8])
        self.assertEqual(cfg["plugins"]["homeassistant"]["token"], "SECRET")
        self.assertEqual(V3["version"], 3, "input must not be modified")

    def test_v1_to_v5(self):
        cfg, _, _ = migrate.migrate(V1)
        schema.validate(cfg)
        keys = cfg["layouts"][0]["pages"][0]["keys"]
        self.assertEqual(keys["2"]["tap"][0]["params"]["keys"], "ctrl+c")
        self.assertEqual(cfg["layouts"][0]["buttons"]["37"]["tap"][0]["action"], "url")

    def test_example_config_from_1x(self):
        with open(os.path.join(HERE, "fixtures", "config-1x-example.json"), encoding="utf-8") as f:
            cfg, _, _ = migrate.migrate(json.load(f))
        schema.validate(cfg)
        self.assertTrue(cfg["layouts"][0]["pages"][1]["folder"])

    def test_default_config_valid(self):
        schema.validate(migrate.default_config())


class TestValidation(unittest.TestCase):
    def bad(self, mutate, msg):
        cfg = migrate.default_config()
        mutate(cfg)
        with self.assertRaises(schema.ConfigError) as cm:
            schema.validate(cfg)
        self.assertIn(msg.lower(), str(cm.exception).lower())

    def test_errors(self):
        self.bad(lambda c: c["layouts"][0]["pages"][0].update(folder=True), "first page")
        self.bad(lambda c: c["layouts"][0]["pages"][0]["keys"].update({"16": {}}), "must be 1-15")
        self.bad(lambda c: c["layouts"][0]["pages"][0]["keys"]["1"].update(color=[1, 2]), "color")
        self.bad(lambda c: c["layouts"][0]["pages"][0]["keys"]["1"].update(tap=[{"wait": 99}]), "wait")
        self.bad(lambda c: c["layouts"][0]["pages"][0]["keys"]["1"].update(tap=[{"plugin": 1}]), "step needs")
        self.bad(lambda c: c["layouts"][0]["pages"][0]["keys"]["1"].update(image="../../etc/passwd"), "image")
        self.bad(lambda c: c["layouts"][0]["pages"].append(dict(c["layouts"][0]["pages"][0])), "unique id")
        self.bad(lambda c: c["devices"]["m18"].update(layout="nope"), "unknown layout")
        self.bad(lambda c: c["profiles"]["rules"].append({"app": "obs64.exe", "page": "missing"}), "app rule")
        self.bad(lambda c: c.update(brightness=101), "brightness")
        self.bad(lambda c: c["layouts"][0]["buttons"].update({"3": {}}), "button id")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.d = tmpdir(V3)
        self.s = store.Store(self.d, {"homeassistant": {"token"}})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_migrates_and_keeps_old_in_history(self):
        h = self.s.history()
        self.assertEqual(len(h), 1)
        with open(os.path.join(self.d, "history", h[0]["id"]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], 3)
        with open(os.path.join(self.d, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], 5)

    def test_undo_and_restore(self):
        self.s.update(lambda c: c.update(brightness=10))
        self.s.update(lambda c: c.update(brightness=20))
        self.assertEqual(self.s.get()["brightness"], 20)
        self.s.undo()
        self.assertEqual(self.s.get()["brightness"], 10)
        self.s.undo()
        self.assertEqual(self.s.get()["brightness"], 70)
        oldest = self.s.history()[0]["id"]
        self.s.restore(oldest)  # the v3 original
        self.assertEqual(self.s.get()["version"], 5)
        with self.assertRaises(schema.ConfigError):
            self.s.restore("../config.json")

    def test_history_is_capped(self):
        for i in range(store.HISTORY_KEEP + 5):
            self.s.update(lambda c, i=i: c.update(brightness=i))
        self.assertEqual(len(self.s.history()), store.HISTORY_KEEP)

    def test_bad_save_is_refused_and_file_untouched(self):
        before = open(os.path.join(self.d, "config.json"), encoding="utf-8").read()
        with self.assertRaises(schema.ConfigError):
            self.s.update(lambda c: c.update(layouts=[]))
        self.assertEqual(open(os.path.join(self.d, "config.json"), encoding="utf-8").read(), before)
        self.assertEqual(self.s.get()["brightness"], 70)

    def test_export_strips_secrets_and_import_keeps_mine(self):
        exp = self.s.export()
        self.assertNotIn("SECRET", json.dumps(exp))
        self.assertIn("SECRET", json.dumps(self.s.export(include_secrets=True)))
        exp["config"]["brightness"] = 33
        self.s.import_(exp)
        self.assertEqual(self.s.get()["brightness"], 33)
        self.assertEqual(self.s.get()["plugins"]["homeassistant"]["token"], "SECRET")
        self.s.import_(V1)  # an old raw config works too
        self.assertEqual(self.s.get()["layouts"][0]["pages"][0]["keys"]["1"]["label"], "Play")
        with self.assertRaises(schema.ConfigError):
            self.s.import_({"hello": 1})

    def test_strip_secrets(self):
        cfg, was = self.s.strip_secrets(self.s.get())
        self.assertNotIn("token", cfg["plugins"]["homeassistant"])
        self.assertEqual(was, {"homeassistant": ["token"]})


class TestNavigator(unittest.TestCase):
    PAGES = [{"id": "aa1", "name": "A"}, {"id": "ff1", "name": "F", "folder": True}, {"id": "bb1", "name": "B"}]

    def test_folders(self):
        n = Navigator(self.PAGES)
        n.step(1)
        self.assertEqual(n.current_id, "bb1")  # skips the folder
        n.open("ff1")
        self.assertEqual(n.current_id, "ff1")
        n.back()
        self.assertEqual(n.current_id, "bb1")
        n.open("ff1")
        n.step(1)
        self.assertEqual(n.current_id, "aa1")  # from inside a folder: relative to where it was opened
        with self.assertRaises(ValueError):
            n.open("zzz")

    def test_reconfigure_keeps_page(self):
        n = Navigator(self.PAGES)
        n.goto_id("bb1")
        n.configure([self.PAGES[2], self.PAGES[0]])
        self.assertEqual(n.current_id, "bb1")
        n.configure([self.PAGES[0]])
        self.assertEqual(n.current_id, "aa1")

    def test_manual_version(self):
        n = Navigator(self.PAGES)
        mv = n.manual_version
        n.goto_id("bb1", manual=False)
        self.assertEqual(n.manual_version, mv)
        n.step(1)
        self.assertEqual(n.manual_version, mv + 1)


class TestGestures(unittest.TestCase):
    def setUp(self):
        self.app = RecordingApp()
        self.keys = {}
        self.g = actions.Gestures(self.app, "d", None, lambda b: self.keys.get(b))

    def ran(self):
        with self.app.lock:
            return list(self.app.ran)

    def test_plain_tap_fires_on_press(self):
        self.keys[1] = {"tap": [{"plugin": "x", "action": "tap"}]}
        self.g.press(1)
        self.assertTrue(wait_for(lambda: self.ran() == ["tap"], 1))
        self.g.release(1)

    def test_steps_and_waits(self):
        self.keys[1] = {"tap": [{"plugin": "x", "action": "a"}, {"wait": 0.1}, {"plugin": "x", "action": "b"}]}
        t = time.time()
        self.g.press(1)
        self.assertTrue(wait_for(lambda: self.ran() == ["a", "b"], 2))
        self.assertGreaterEqual(time.time() - t, 0.1)

    def test_hold_vs_tap(self):
        self.keys[1] = {"tap": [{"plugin": "x", "action": "tap"}], "hold": [{"plugin": "x", "action": "hold"}]}
        self.g.press(1)
        time.sleep(0.05)
        self.g.release(1)
        self.assertTrue(wait_for(lambda: self.ran() == ["tap"], 1))
        self.g.press(1)
        time.sleep(actions.HOLD_S + 0.15)
        self.g.release(1)
        self.assertTrue(wait_for(lambda: self.ran() == ["tap", "hold"], 1))
        time.sleep(0.1)
        self.assertEqual(self.ran(), ["tap", "hold"], "a hold must not also tap")

    def test_double(self):
        self.keys[1] = {"tap": [{"plugin": "x", "action": "tap"}], "double": [{"plugin": "x", "action": "double"}]}
        for _ in range(2):
            self.g.press(1)
            self.g.release(1)
        self.assertTrue(wait_for(lambda: self.ran() == ["double"], 1))
        time.sleep(actions.DOUBLE_S + 0.1)
        self.assertEqual(self.ran(), ["double"])
        self.g.press(1)
        self.g.release(1)
        self.assertTrue(wait_for(lambda: self.ran() == ["double", "tap"], 1))

    def test_repeat_stops_on_disconnect(self):
        """1.x bug: a held repeat key kept running forever after the pad dropped."""
        self.keys[1] = {"repeat": True, "tap": [{"plugin": "x", "action": "vol"}]}
        self.g.press(1)
        self.assertTrue(wait_for(lambda: len(self.ran()) >= 3, 2))
        self.g.cancel_all()  # what the device runner does when the connection drops
        time.sleep(0.15)
        n = len(self.ran())
        time.sleep(0.4)
        self.assertEqual(len(self.ran()), n)

    def test_repeat_stops_on_release(self):
        self.keys[1] = {"repeat": True, "tap": [{"plugin": "x", "action": "vol"}]}
        self.g.press(1)
        self.g.release(1)
        time.sleep(actions.REPEAT_DELAY + 0.2)
        self.assertEqual(self.ran(), ["vol"])


class TestDevices(unittest.TestCase):
    def test_m18_rows_flip(self):
        self.assertEqual([m18.image_index(s) for s in (1, 5, 6, 11, 15)], [11, 15, 6, 1, 5])

    def test_streamdeck_reports(self):
        jpeg = bytes(range(256)) * 9  # 2304 bytes -> 3 reports
        reps = streamdeck.image_reports(4, jpeg)
        self.assertEqual(len(reps), 3)
        self.assertTrue(all(len(r) == 1024 for r in reps))
        self.assertEqual(list(reps[0][:8]), [0x02, 0x07, 4, 0, 0xF8, 0x03, 0, 0])
        self.assertEqual(list(reps[2][:8]), [0x02, 0x07, 4, 1, (2304 - 2032) & 0xFF, (2304 - 2032) >> 8, 2, 0])
        self.assertEqual(b"".join(r[8:8 + (r[4] | r[5] << 8)] for r in reps), jpeg)

    def test_streamdeck_states(self):
        self.assertEqual(streamdeck.parse_states([1, 0, 15, 0] + [0, 1] + [0] * 13, 15)[:3], [False, True, False])
        self.assertIsNone(streamdeck.parse_states([1, 3, 0, 0], 15))  # dial report on a Plus

    def test_streamdeck_info(self):
        i = streamdeck.info_for({"product_id": 0x006C, "serial_number": "CL12-34!", "path": b"x"})
        self.assertEqual((i.cols, i.rows, i.key_px, i.id), (8, 4, 96, "sd-CL1234"))
        self.assertIsNone(streamdeck.info_for({"product_id": 0x0086}))  # Pedal: no screens
        m = streamdeck.info_for({"product_id": 0x0063, "serial_number": "MINI1", "path": b"m"})
        self.assertEqual((m.model, m.cols, m.rows, m.key_px, m.buttons), ("Stream Deck Mini", 3, 2, 80, []))
        self.assertEqual(streamdeck.info_for({"product_id": 0x0090, "serial_number": "M2"}).key_count, 6)

    def test_mini_reports(self):
        bmp = streamdeck.mini_encode(Image.new("RGB", (216, 216), (255, 0, 0)))
        self.assertEqual(bmp[:2], b"BM")
        self.assertEqual(len(bmp), 54 + 80 * 80 * 3)
        reps = streamdeck.mini_image_reports(2, bmp)
        self.assertEqual(len(reps), -(-len(bmp) // 1008))
        self.assertTrue(all(len(r) == 1024 for r in reps))
        self.assertEqual(list(reps[0][:6]), [0x02, 0x01, 0, 0, 0, 3])   # key numbers start at 1 on the Mini
        self.assertEqual(list(reps[-1][:6]), [0x02, 0x01, len(reps) - 1, 0, 1, 3])
        self.assertEqual(b"".join(r[16:16 + 1008] for r in reps)[:len(bmp)], bmp)

    def test_mini_orientation(self):
        # top-left red corner on the key -> after rotate 90 + flip top-bottom it sits at the top-left of the raw image
        img = Image.new("RGB", (80, 80), (0, 0, 0))
        img.paste((255, 0, 0), (0, 0, 20, 10))  # wide red bar in the top-left
        raw = Image.open(io.BytesIO(streamdeck.mini_encode(img)))
        self.assertEqual(raw.getpixel((5, 15)), (255, 0, 0))   # now tall: rotated a quarter turn
        self.assertEqual(raw.getpixel((15, 5)), (0, 0, 0))

    def test_mini_states(self):
        self.assertEqual(streamdeck.mini_parse_states([1, 0, 1, 0, 0, 0, 1], 6),
                         [False, True, False, False, False, True])
        self.assertIsNone(streamdeck.mini_parse_states([2, 1], 6))

    def test_streamdeck_driver_with_fake_hid(self):
        class FakeHid:
            def __init__(self):
                self.writes, self.features, self.reads = [], [], []
            def device(self):
                return self
            def open_path(self, p):
                self.path = p
            def set_nonblocking(self, n):
                pass
            def write(self, b):
                self.writes.append(bytes(b))
                return len(b)
            def send_feature_report(self, b):
                self.features.append(bytes(b))
            def read(self, n):
                return self.reads.pop(0) if self.reads else []
            def close(self):
                pass
        hidm = FakeHid()
        info = streamdeck.info_for({"product_id": 0x0080, "serial_number": "A1", "path": b"p"})
        dev = streamdeck.StreamDeck(info, hidm)
        dev.open()
        dev.set_brightness(55)
        self.assertEqual(hidm.features[-1][:3], b"\x03\x08\x37")
        dev.set_key(3, Image.new("RGB", (216, 216), (255, 0, 0)))
        self.assertTrue(hidm.writes and all(w[2] == 2 for w in hidm.writes))
        hidm.reads = [[1, 0, 15, 0] + [0, 0, 1] + [0] * 12, [1, 0, 15, 0] + [0] * 15]
        self.assertEqual(dev.read_events(), [(3, True), (3, False)])

        hidm = FakeHid()
        info = streamdeck.info_for({"product_id": 0x0063, "serial_number": "M1", "path": b"m"})
        dev = __import__("vsdpad.devices", fromlist=["x"]).open_device(info, hidm)
        self.assertIsInstance(dev, streamdeck.StreamDeckMini)
        dev.open()
        dev.set_brightness(40)
        self.assertEqual(hidm.features[-1], bytes([0x05, 0x55, 0xAA, 0xD1, 0x01, 40]).ljust(17, b"\x00"))
        dev.clear()
        self.assertEqual(hidm.features[-1][:2], b"\x0b\x63")
        dev.set_key(6, Image.new("RGB", (216, 216), (0, 0, 255)))
        self.assertTrue(hidm.writes and all(w[:2] == b"\x02\x01" and w[5] == 6 for w in hidm.writes))
        hidm.reads = [[1, 0, 0, 0, 0, 1, 0], [1, 0, 0, 0, 0, 0, 0]]
        self.assertEqual(dev.read_events(), [(5, True), (5, False)])

    def test_discover(self):
        class H:
            @staticmethod
            def enumerate():
                return [{"vendor_id": 0x5548, "product_id": 0x1000, "path": b"a"},
                        {"vendor_id": 0x5548, "product_id": 0x1000, "path": b"b"},  # second interface, same pad
                        {"vendor_id": 0x0FD9, "product_id": 0x0080, "serial_number": "S1", "path": b"c"},
                        {"vendor_id": 0x0FD9, "product_id": 0x0063, "serial_number": "S2", "path": b"d"},
                        {"vendor_id": 0x0FD9, "product_id": 0x0086, "serial_number": "S3", "path": b"e"}]
        infos, notes = __import__("vsdpad.devices", fromlist=["x"]).discover(H)
        self.assertEqual([i.id for i in infos], ["m18", "sd-S1", "sd-S2"])
        self.assertEqual(infos[2].model, "Stream Deck Mini")
        self.assertTrue(any("Pedal" in n for n in notes))
        self.assertFalse(any("Mini" in n for n in notes))


class TestFitLayout(unittest.TestCase):
    def key(self, label, **kw):
        return dict(label=label, **kw)

    def test_small_pad_packs_and_splits(self):
        from vsdpad import fitlayout
        keys = {str(s): self.key(f"K{s}") for s in (1, 2, 4, 7, 9, 11, 13, 15)}  # 8 keys with gaps
        pages = [{"id": "pmain01", "name": "Main", "folder": False, "keys": keys},
                 {"id": "ptwo01", "name": "Two", "folder": False, "keys": {"14": self.key("Only")}}]
        out = fitlayout.fit_pages(pages, 3, 2, {"pmain01", "ptwo01"})
        self.assertEqual([p["name"] for p in out], ["Main", "Main 2", "Two"])
        self.assertEqual([k["label"] for _, k in sorted(out[0]["keys"].items())], ["K1", "K2", "K4", "K7", "K9", "Next"])
        self.assertEqual([out[1]["keys"][s]["label"] for s in ("1", "2", "3", "4")], ["K11", "K13", "K15", "Next"])
        self.assertEqual(out[2]["keys"], {"1": {"label": "Only"}})
        self.assertFalse({p["id"] for p in out} & {"pmain01", "ptwo01"})
        self.assertEqual(pages[0]["keys"]["1"], {"label": "K1"}, "the source pages are left alone")

    def test_folders_chain_and_links_follow(self):
        from vsdpad import fitlayout
        opener = {"label": "Lights", "tap": [{"plugin": "pages", "action": "open", "params": {"page": "pfold01"}}]}
        pages = [{"id": "pmain01", "name": "Main", "folder": False, "keys": {"1": opener}},
                 {"id": "pfold01", "name": "Lights", "folder": True,
                  "keys": {str(s): self.key(f"L{s}") for s in range(1, 9)}}]
        out = fitlayout.fit_pages(pages, 3, 2)
        a, b = out[1], out[2]
        self.assertTrue(a["folder"] and b["folder"])
        self.assertEqual(out[0]["keys"]["1"]["tap"][0]["params"]["page"], a["id"])
        self.assertEqual(a["keys"]["6"]["tap"][0], {"plugin": "pages", "action": "open", "params": {"page": b["id"]}})
        self.assertEqual(b["keys"]["4"]["tap"][0]["action"], "back")
        self.assertEqual(opener["tap"][0]["params"]["page"], "pfold01")

    def test_bigger_pad_keeps_positions(self):
        from vsdpad import fitlayout
        lay = {"id": "main", "name": "Main", "cols": 5, "rows": 3, "buttons": {},
               "pages": [{"id": "pmain01", "name": "Main", "folder": False, "keys": {"6": {"label": "row2"}}}]}
        new = fitlayout.starter_layout([lay], "xl", "Stream Deck XL", 8, 4)
        self.assertEqual(new["pages"][0]["keys"], {"9": {"label": "row2"}})

    def test_starter_layout_is_valid_config(self):
        from vsdpad import fitlayout
        cfg = migrate.default_config()
        cfg["layouts"].append(fitlayout.starter_layout(cfg["layouts"], "mini", "Stream Deck Mini", 3, 2))
        schema.validate(cfg)


class TestRender(unittest.TestCase):
    def test_blank_modern_is_none_and_themes_paint(self):
        self.assertIsNone(render.render_key("modern", {}))
        img = render.render_key("nuclear", {}, slot=7)
        self.assertEqual(img.size, (216, 216))
        img = render.render_key("cyberpunk", {"icon": "♪", "label": "Vol"}, slot=3, cols=8, rows=4)
        self.assertEqual(img.size, (216, 216))

    def test_live_and_picture(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.png")
        Image.new("RGB", (300, 200), (0, 200, 0)).save(p)
        img = render.render_key("modern", {"label": "Pic"}, None, 1, 5, 3, 0, p)
        self.assertGreater(img.getpixel((108, 60))[1], 150)  # the picture shows
        img = render.render_key("modern", {"label": "T"}, {"text": "12:00", "progress": 0.5, "on": False})
        self.assertEqual(img.size, (216, 216))

    def test_cache(self):
        r = render.Renderer()
        s1, a = r.render("modern", {"label": "A"}, None, 1, 5, 3)
        s2, b = r.render("modern", {"label": "A"}, None, 2, 5, 3)
        self.assertEqual(s1, s2)  # same look on modern regardless of position
        self.assertIs(a, b)
        s3, _ = r.render("nuclear", {"label": "A"}, None, 1, 5, 3)
        s4, _ = r.render("nuclear", {"label": "A"}, None, 2, 5, 3)
        self.assertNotEqual(s3, s4)  # wallpaper slices differ


class TestApp(unittest.TestCase):
    def setUp(self):
        cfg = migrate.default_config()
        cfg["layouts"][0]["pages"].append({"id": "ptwo01", "name": "Two", "folder": False, "keys": {
            "1": {"label": "Back home", "tap": [{"plugin": "pages", "action": "home", "params": {}}]}}})
        self.d = tmpdir(cfg)
        self.m = fake.FakeDevice(fake.info_for(id="m18"))
        self.xl = fake.FakeDevice(fake.info_for(cols=8, rows=4, buttons=(), id="sd-XL", model="Stream Deck XL"))
        self.app = App(self.d, fake_devices=[self.m, self.xl])
        self.app.start()
        self.assertTrue(wait_for(lambda: len(self.m.images) == 15 and len(self.xl.images) == 32))

    def tearDown(self):
        self.app.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_new_device_gets_its_own_pages(self):
        cfg = self.app.store.get()
        self.assertEqual(cfg["devices"]["sd-XL"]["layout"], cfg["layouts"][1]["id"])
        self.assertEqual((cfg["layouts"][1]["cols"], cfg["layouts"][1]["rows"]), (8, 4))
        self.assertTrue(cfg["layouts"][1]["pages"][0]["keys"], "starts with a copy of the main keys, not a blank page")

    def test_mini_gets_main_keys_fitted(self):
        mini = fake.FakeDevice(fake.info_for(cols=3, rows=2, buttons=(), id="sd-MINI", model="Stream Deck Mini"))
        self.app.ensure_device(mini.info)
        cfg = self.app.store.get()
        lay = next(l for l in cfg["layouts"] if l["id"] == cfg["devices"]["sd-MINI"]["layout"])
        self.assertEqual((lay["cols"], lay["rows"]), (3, 2))
        self.assertTrue(all(1 <= int(s) <= 6 for p in lay["pages"] for s in p["keys"]))
        self.assertTrue(lay["pages"][0]["keys"])

    def test_only_changed_keys_are_resent(self):
        n = len(self.m.log)
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"]["3"].update(label="Quiet"))
        self.assertTrue(wait_for(lambda: 3 in self.m.log[n:]))
        time.sleep(0.2)
        self.assertEqual([s for s in self.m.log[n:] if s != 5], [3])  # slot 5 is the live clock, it may tick

    def test_buttons_switch_page_per_device(self):
        self.m.press(49)
        self.m.press(49, False)
        r = self.app.runner("m18")
        self.assertTrue(wait_for(lambda: r.nav.current_id == "ptwo01"))
        self.assertEqual(self.app.runner("sd-XL").nav.current, 0)
        self.m.press(1)
        self.m.press(1, False)
        self.assertTrue(wait_for(lambda: r.nav.current_id == "pmain01"))

    def test_reconnect_redraws_and_cancels(self):
        r = self.app.runner("m18")
        self.m.fail_next = OSError("read error")
        self.assertTrue(wait_for(lambda: not r.connected, 2))
        self.assertEqual(self.app.status()["devices"][0]["status"] if self.app.status()["devices"][0]["id"] == "m18"
                         else "reconnecting", "reconnecting")
        self.assertTrue(wait_for(lambda: r.connected, 5))

    def test_failed_key_shows_a_warning_then_recovers(self):
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"].update(
            {"7": {"label": "Broken", "tap": [{"plugin": "nope", "action": "x"}]}}))
        self.assertTrue(wait_for(lambda: 7 in self.m.log))
        r = self.app.runner("m18")
        n = len(self.m.log)
        self.m.press(7)
        self.m.press(7, False)
        self.assertTrue(wait_for(lambda: 7 in r.flash and 7 in self.m.log[n:], 2))
        self.assertTrue(any("not installed" in e["message"] for e in self.app.status()["errors"]))
        n = len(self.m.log)
        self.assertTrue(wait_for(lambda: 7 in self.m.log[n:], 4), "the key goes back to normal after the warning")
        self.assertEqual(r.flash, {})

    def test_live_key_updates(self):
        self.assertTrue(wait_for(lambda: self.app.live.get({"plugin": "clock", "widget": "time", "params": {}}) is not None))

    def test_dim_when_idle_and_wake(self):
        self.app.store.update(lambda c: c["idle"].update(dim_after_min=1, dim_to=5))
        r = self.app.runner("m18")
        r.last_input -= 61
        self.assertTrue(wait_for(lambda: self.m.brightness == 5))
        self.m.press(49)
        self.m.press(49, False)
        self.assertTrue(wait_for(lambda: self.m.brightness == 80))
        time.sleep(0.2)
        self.assertEqual(r.nav.current, 0, "the waking press must not run its action")


class TestProfiles(unittest.TestCase):
    def test_match(self):
        rules = [{"app": "obs64.exe", "page": "a"}, {"title": "youtube", "page": "b"}, {"app": "game", "page": "c"}]
        self.assertEqual(profiles.match(rules, "OBS64.EXE", "x")["page"], "a")
        self.assertEqual(profiles.match(rules, "chrome.exe", "Cats - YouTube")["page"], "b")
        self.assertEqual(profiles.match(rules, "game.exe", "")["page"], "c")
        self.assertIsNone(profiles.match(rules, "notepad.exe", "x"))

    def test_switch_and_revert_without_fighting(self):
        nav = Navigator([{"id": "aa1", "name": "A"}, {"id": "obs", "name": "OBS"}, {"id": "cc1", "name": "C"}])

        class R:
            pass
        r = R()
        r.nav = nav

        class FakeApp:
            class store:
                @staticmethod
                def get():
                    return {"profiles": {"enabled": True, "rules": [{"app": "obs64.exe", "page": "obs", "revert": True}]}}
            @staticmethod
            def runners_snapshot():
                return [("m18", r)]
        fg = ["explorer.exe"]
        p = profiles.Profiles(FakeApp, probe=lambda: (fg[0], ""))
        p.tick()
        fg[0] = "obs64.exe"; p.tick()
        self.assertEqual(nav.current_id, "obs")
        fg[0] = "explorer.exe"; p.tick()
        self.assertEqual(nav.current_id, "aa1")  # went back
        fg[0] = "obs64.exe"; p.tick()
        nav.goto_id("cc1")  # the user picks a page by hand
        fg[0] = "explorer.exe"; p.tick()
        self.assertEqual(nav.current_id, "cc1")  # left alone


class TestPlugins(unittest.TestCase):
    def test_timer(self):
        t = timer.Timer()
        ctx = actions.Context(None)
        t.run("start", {"name": "tea", "minutes": 1}, ctx, now=100)
        self.assertEqual(t.widget("countdown", {"name": "tea"}, ctx, now=100)["text"], "01:00")
        self.assertEqual(t.widget("countdown", {"name": "tea"}, ctx, now=130)["text"], "00:30")
        t.run("toggle", {"name": "tea"}, ctx, now=130)
        w = t.widget("countdown", {"name": "tea"}, ctx, now=500)
        self.assertEqual((w["text"], w.get("sub")), ("00:30", "paused"))
        t.run("toggle", {"name": "tea"}, ctx, now=500)
        self.assertEqual(t.widget("countdown", {"name": "tea"}, ctx, now=531)["text"], "DONE")
        t.run("reset", {"name": "tea"}, ctx)
        self.assertEqual(t.widget("countdown", {"name": "tea"}, ctx)["text"], "--:--")

    def test_clock(self):
        c = clock.Clock()
        ts = time.mktime((2026, 11, 7, 21, 5, 9, 0, 0, -1))
        self.assertEqual(c.widget("time", {}, None, now=ts)["text"], "21:05")
        self.assertEqual(c.widget("time", {"format": "12"}, None, now=ts), {"text": "9:05", "sub": "PM"})
        self.assertEqual(c.widget("time", {"format": "24s"}, None, now=ts)["text"], "21:05:09")

    def test_steam_library(self):
        root = tempfile.mkdtemp()
        lib2 = os.path.join(root, "lib2")
        os.makedirs(os.path.join(root, "steamapps"))
        os.makedirs(os.path.join(lib2, "steamapps"))
        os.makedirs(os.path.join(root, "appcache", "librarycache", "620"))
        open(os.path.join(root, "appcache", "librarycache", "620", "library_600x900.jpg"), "wb").close()
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), "w") as f:
            f.write('"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n' % (root.replace("\\", "\\\\"), lib2.replace("\\", "\\\\")))
        for lib, appid, name in ((root, "620", "Portal 2"), (lib2, "228980", "Steamworks Common Redistributables"), (lib2, "70", "Half-Life")):
            with open(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf"), "w") as f:
                f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n\t"LastPlayed"\t"5"\n}\n' % (appid, name))
        games = steam.installed_games(root)
        self.assertEqual([g["name"] for g in games], ["Half-Life", "Portal 2"])
        self.assertTrue(steam.find_art(root, "620").endswith("library_600x900.jpg"))
        self.assertIsNone(steam.find_art(root, "70"))


class TestThemeImport(unittest.TestCase):
    def make_zip(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n, b in files.items():
                z.writestr(n, b)
        return buf.getvalue()

    def png(self):
        b = io.BytesIO()
        Image.new("RGB", (64, 64), (1, 2, 3)).save(b, "PNG")
        return b.getvalue()

    def test_good_and_hostile(self):
        d = tempfile.mkdtemp()
        pack = json.dumps({"id": "mine", "name": "Mine", "wallpaper": "wp.png", "style": {"dim": 0.2}})
        tid = themes_io.import_zip(self.make_zip({"x/pack.json": pack, "x/wp.png": self.png(), "../../evil.png": self.png(),
                                                  "run.exe": b"MZ"}), d)
        self.assertEqual(tid, "mine")
        self.assertEqual(sorted(os.listdir(os.path.join(d, "mine"))), ["evil.png", "pack.json", "wp.png"])  # flattened, exe dropped
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(d), "evil.png")))
        with self.assertRaises(ValueError):
            themes_io.import_zip(self.make_zip({"pack.json": pack, "wp.png": self.png()}), d)  # already exists
        with self.assertRaises(ValueError):
            themes_io.import_zip(self.make_zip({"pack.json": json.dumps({"id": "../x"})}), d)
        with self.assertRaises(ValueError):
            themes_io.import_zip(self.make_zip({"pack.json": json.dumps({"id": "y", "wallpaper": "w.png"}), "w.png": b"not a png"}), d)
        with self.assertRaises(ValueError):
            themes_io.import_zip(b"not a zip", d)
        self.assertTrue(zipfile.ZipFile(io.BytesIO(themes_io.export_zip("mine", d))).namelist())


if __name__ == "__main__":
    unittest.main(verbosity=1)
