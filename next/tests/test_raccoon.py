"""The raccoon mascot key: moods, pictures and the live hook that puts its picture on a key."""
import datetime as dt, os, shutil, sys, tempfile, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad import live, render  # noqa: E402
from vsdpad.actions import Context  # noqa: E402
from vsdpad.plugins import raccoon  # noqa: E402
from vsdpad.plugins import _raccoon_art as art  # noqa: E402


class FakePlugin:
    settings = []

    def __init__(self, widgets):
        self.widgets = {w: {} for w in widgets}
        self.out = {w: {"on": False} for w in widgets}

    def widget(self, name, params, ctx):
        return self.out[name]


class FakeStore:
    def __init__(self):
        self.cfg = {}

    def get(self):
        return self.cfg

    def plugin_settings(self, pid):
        return {}


class FakeLive:
    def __init__(self):
        self.pokes = 0

    def poke(self, pid):
        self.pokes += 1


class FakeApp:
    def __init__(self):
        self.obs, self.np = FakePlugin(["stream", "record"]), FakePlugin(["now"])
        self.plugins = {"obs": self.obs, "nowplaying": self.np}
        self.store, self.live, self.daynight = FakeStore(), FakeLive(), None


class TestRaccoon(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.r = raccoon.Raccoon()
        self.r.dir = tempfile.mkdtemp()
        self.ctx = Context(self.app, settings={"sleep_from": "23:00", "sleep_until": "07:00"})
        self.noon = dt.datetime(2026, 11, 8, 12, 0)

    def tearDown(self):
        shutil.rmtree(self.r.dir, ignore_errors=True)

    def test_moods_in_order(self):
        self.assertEqual(self.r.mood(self.ctx, self.noon), "idle")
        self.assertEqual(self.r.mood(self.ctx, dt.datetime(2026, 11, 8, 2, 0)), "night")
        self.app.np.out["now"] = {"on": True}
        self.assertEqual(self.r.mood(self.ctx, dt.datetime(2026, 11, 8, 2, 0)), "music")  # music wakes it up
        self.app.obs.out["record"] = {"on": True}
        self.assertEqual(self.r.mood(self.ctx, self.noon), "live")
        self.r.run("pet", {}, self.ctx)
        self.assertEqual(self.r.mood(self.ctx, self.noon), "happy")
        self.assertEqual(self.app.live.pokes, 1)
        self.r.pet_until = time.monotonic() - 1
        self.assertEqual(self.r.mood(self.ctx, self.noon), "live")

    def test_broken_or_unset_plugins_are_ignored(self):
        def boom(*a):
            raise OSError("OBS is off")
        self.app.obs.widget = boom
        self.app.np.settings = [{"key": "x"}]  # has settings, none filled in: never asked
        self.app.np.out["now"] = {"on": True}
        self.assertEqual(self.r.mood(self.ctx, self.noon), "idle")

    def test_widget_gives_a_picture(self):
        self.ctx.settings = {"sleep_from": "00:00", "sleep_until": "00:00"}  # never sleeps, whatever the clock says
        self.assertEqual(self.r.widget("mood", {}, self.ctx), {"image": "raccoon:idle"})
        self.assertEqual(self.r.widget("mood", {"names": True}, self.ctx)["label"], "Raccoon")

    def test_pictures_are_animated_gifs(self):
        for m in art.MOODS:
            p = self.r.image(m)
            self.assertTrue(os.path.exists(p))
            self.assertTrue(render.animated(p), m)
        self.assertIsNone(self.r.image("../evil"))

    def test_sleep_hours_wrap_midnight(self):
        s, e = dt.time(23), dt.time(7)
        self.assertTrue(raccoon.asleep(dt.datetime(2026, 1, 1, 23, 30), s, e))
        self.assertTrue(raccoon.asleep(dt.datetime(2026, 1, 1, 3, 0), s, e))
        self.assertFalse(raccoon.asleep(dt.datetime(2026, 1, 1, 12, 0), s, e))

    def test_live_picture_reaches_the_key(self):
        self.assertEqual(live.clean({"image": "raccoon:music", "label": "x"})["image"], "raccoon:music")
        self.assertNotIn("image", live.clean({"image": "../../etc/passwd"}))
        p = self.r.image("music")
        rr = render.Renderer(lambda ref: p if ref == "raccoon:music" else None)
        key = {"live": {"plugin": "raccoon", "widget": "mood"}}
        lv = {"image": "raccoon:music"}
        self.assertTrue(rr.is_animated(key, lv))
        a = rr.render("modern", key, lv, 1, 5, 3, t=0)[1]
        b = rr.render("modern", key, lv, 1, 5, 3, t=0.6)[1]
        self.assertIsNotNone(a)
        self.assertNotEqual(a.tobytes(), b.tobytes())  # it moves


if __name__ == "__main__":
    unittest.main(verbosity=1)
