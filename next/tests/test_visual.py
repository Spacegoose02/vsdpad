"""Animated keys, a photo across a page, per-page themes and day/night."""
import datetime as dt, json, os, shutil, sys, tempfile, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image  # noqa: E402

from vsdpad import daynight, migrate, render, schema  # noqa: E402
from vsdpad.app import App  # noqa: E402
from vsdpad.devices import fake  # noqa: E402

UTC = dt.timezone.utc


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def make_gif(path, colors=((255, 0, 0), (0, 255, 0), (0, 0, 255)), ms=200):
    frames = [Image.new("RGB", (64, 64), c) for c in colors]
    frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], duration=ms, loop=0)


class TestSun(unittest.TestCase):
    def test_london_midsummer_and_midwinter(self):
        rise, set_ = daynight.sun_times(51.5, -0.13, dt.date(2026, 6, 21))
        self.assertEqual((rise.hour, set_.hour), (3, 20))
        rise, set_ = daynight.sun_times(51.5, -0.13, dt.date(2026, 12, 21))
        self.assertEqual((rise.hour, set_.hour), (8, 15))

    def test_polar_day_and_night(self):
        self.assertEqual(daynight.sun_times(78, 15, dt.date(2026, 6, 21)), "day")
        self.assertEqual(daynight.sun_times(78, 15, dt.date(2026, 12, 21)), "night")


class TestDayNight(unittest.TestCase):
    CFG = {"brightness": 80, "daynight": {"enabled": True, "mode": "times", "day_start": "07:00", "night_start": "20:00",
                                          "night_theme": "campfire", "day_brightness": 90, "night_brightness": 20, "fade_min": 30}}

    def at(self, h, m=0, cfg=None):
        return daynight.DayNight().compute(cfg or self.CFG, dt.datetime(2026, 11, 8, h, m, tzinfo=UTC))

    def test_switches_theme_and_fades_brightness(self):
        self.assertEqual(self.at(12), {"theme": None, "brightness": 90, "day": True, "light": 1.0})
        self.assertEqual(self.at(23)["theme"], "campfire")
        self.assertEqual(self.at(23)["brightness"], 20)
        mid = self.at(19, 55)["brightness"]
        self.assertTrue(20 < mid < 90)

    def test_off_changes_nothing(self):
        r = self.at(23, cfg={"daynight": {"enabled": False}})
        self.assertEqual((r["theme"], r["brightness"]), (None, None))

    def test_night_shift_wraps_midnight(self):
        cfg = {"daynight": {"enabled": True, "mode": "times", "day_start": "20:00", "night_start": "06:00", "fade_min": 0,
                            "night_theme": "midnight"}}
        self.assertIsNone(self.at(23, cfg=cfg)["theme"])
        self.assertEqual(self.at(12, cfg=cfg)["theme"], "midnight")

    def test_schema(self):
        cfg = migrate.default_config()
        cfg["daynight"] = dict(self.CFG["daynight"])
        schema.validate(cfg)
        for bad in ({"mode": "moon"}, {"day_start": "25:00"}, {"night_theme": "Bad Theme"}, {"day_brightness": 101}):
            c = json.loads(json.dumps(cfg))
            c["daynight"].update(bad)
            with self.assertRaises(schema.ConfigError):
                schema.validate(c)
        c = json.loads(json.dumps(cfg))
        c["layouts"][0]["pages"][0].update(theme="synthwave", backdrop="img:" + "a" * 24 + ".png")
        schema.validate(c)
        c["layouts"][0]["pages"][0]["backdrop"] = "../etc/passwd"
        with self.assertRaises(schema.ConfigError):
            schema.validate(c)


class TestRender(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_gif_frames(self):
        p = os.path.join(self.d, "a.gif")
        make_gif(p)
        self.assertTrue(render.animated(p))
        self.assertEqual([render.frame_at(p, t) for t in (0, 0.25, 0.45, 0.65)], [0, 1, 2, 0])
        r = render.Renderer(lambda ref: p)
        key = {"image": "img:a.gif"}
        self.assertTrue(r.is_animated(key))
        colors = [r.render("modern", key, None, 1, 5, 3, t=t)[1].getpixel((108, 108)) for t in (0, 0.25, 0.45)]
        self.assertEqual([c.index(max(c)) for c in colors], [0, 1, 2])  # red, green, blue

    def test_fast_gif_is_capped_to_5fps(self):
        p = os.path.join(self.d, "b.gif")
        make_gif(p, ms=20)
        self.assertEqual(render.frame_at(p, 0.1), 0)
        self.assertEqual(render.frame_at(p, 0.21), 1)

    def test_still_picture_not_animated(self):
        p = os.path.join(self.d, "c.png")
        Image.new("RGB", (32, 32), (9, 9, 9)).save(p)
        self.assertFalse(render.animated(p))
        self.assertEqual(render.frame_at(p, 3.3), 0)

    def test_backdrop_slices_photo_across_keys(self):
        p = os.path.join(self.d, "photo.png")
        im = Image.new("RGB", (1272, 744), (0, 0, 0))
        im.paste((255, 0, 0), (0, 0, 636, 744))      # left half red
        im.paste((0, 0, 255), (636, 0, 1272, 744))   # right half blue
        im.save(p)
        r = render.Renderer(lambda ref: p if ref == "img:photo" else None)
        left = r.render("modern", None, None, 1, 5, 3, backdrop="img:photo")[1]
        right = r.render("modern", None, None, 5, 5, 3, backdrop="img:photo")[1]
        self.assertIsNotNone(left)  # blank keys show the photo too
        lp, rp = left.getpixel((108, 108)), right.getpixel((108, 108))
        self.assertGreater(lp[0], lp[2])
        self.assertGreater(rp[2], rp[0])
        self.assertIsNone(r.render("modern", None, None, 1, 5, 3)[1])  # no photo: blank stays black


class TestAppVisual(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="vsdpad-test-")
        os.makedirs(os.path.join(self.d, "images"))
        self.gif = "0123456789abcdef0123.gif"
        make_gif(os.path.join(self.d, "images", self.gif))
        cfg = migrate.default_config()
        cfg["layouts"][0]["pages"][0]["keys"]["7"] = {"image": "img:" + self.gif, "label": "Party"}
        with open(os.path.join(self.d, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        self.m = fake.FakeDevice(fake.info_for(id="m18"))
        self.app = App(self.d, fake_devices=[self.m])
        self.app.start()
        self.assertTrue(wait_for(lambda: len(self.m.images) == 15))

    def tearDown(self):
        self.app.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_gif_key_animates_and_pauses_off_page(self):
        n = len(self.m.log)
        self.assertTrue(wait_for(lambda: self.m.log[n:].count(7) >= 3, 3))
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0]["keys"].pop("7"))
        self.assertTrue(wait_for(lambda: not self.app.runner("m18").animating, 3))
        time.sleep(0.3)
        n = len(self.m.log)
        time.sleep(0.6)
        self.assertEqual([s for s in self.m.log[n:] if s != 5], [])  # nothing but the clock redraws

    def test_page_theme_and_daynight_brightness(self):
        n = len(self.m.log)
        self.app.store.update(lambda c: c["layouts"][0]["pages"][0].update(theme="synthwave"))
        self.assertTrue(wait_for(lambda: len(set(self.m.log[n:])) >= 14, 3))  # a wallpaper theme repaints every key
        self.app.store.update(lambda c: c.update(daynight={"enabled": True, "mode": "times", "day_start": "00:00",
                                                          "night_start": "00:01", "fade_min": 0,
                                                          "day_brightness": 33, "night_brightness": 33}))
        self.assertTrue(wait_for(lambda: self.m.brightness == 33, 3))


if __name__ == "__main__":
    unittest.main(verbosity=1)
