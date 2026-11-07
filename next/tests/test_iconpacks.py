"""Icon pack tests (no pad needed).  Run:  python tests/test_iconpacks.py"""
import io, json, os, shutil, sys, tempfile, unittest, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageDraw  # noqa: E402

from vsdpad import iconpacks, render, schema  # noqa: E402

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/></svg>'


def png(color=(255, 255, 255, 255), bg=(0, 0, 0, 0), size=64):
    im = Image.new("RGBA", (size, size), bg)
    ImageDraw.Draw(im).ellipse((8, 8, size - 8, size - 8), fill=color)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def make_zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


class TestBuiltin(unittest.TestCase):
    def test_packs_present_and_licensed(self):
        packs = {p["id"]: p for p in iconpacks.list_packs()}
        for pid in ("outline", "filled", "bold", "duotone", "neon", "pixel"):
            self.assertIn(pid, packs)
            self.assertGreater(packs[pid]["count"], 400)
            self.assertEqual(packs[pid]["license"], "MIT")
            self.assertTrue(os.path.isfile(os.path.join(iconpacks.BUILTIN_DIR, pid, "LICENSE.txt")))

    def test_my_apps_in_every_style(self):
        for pid in ("outline", "filled", "bold", "duotone", "neon", "pixel"):
            self.assertIn("app-wavelength", iconpacks.index(pid)["icons"])
            self.assertIsNotNone(iconpacks.image(f"{pid}:app-bingebandit", 96, (0, 255, 0)))
        self.assertEqual(iconpacks.resolve("theme:app-wishes", {"icon_pack": "pixel"}), "pixel:app-wishes")
        self.assertEqual(len(iconpacks.index("my-apps")["icons"]), 13)

    def test_parse(self):
        self.assertEqual(iconpacks.parse("outline:house"), ("outline", "house"))
        self.assertIsNone(iconpacks.parse("♪"))
        self.assertIsNone(iconpacks.parse("../x:y"))
        self.assertIsNone(iconpacks.parse("a:../../etc"))

    def test_image_is_tinted(self):
        im = iconpacks.image("filled:house", 100, (255, 0, 0))
        self.assertEqual(im.size, (100, 100))
        px = [p for p in list(im.getdata()) if p[3] > 250]
        self.assertTrue(px and all(p[:3] == (255, 0, 0) for p in px))
        self.assertIsNone(iconpacks.image("filled:no-such-icon", 100, (255, 0, 0)))
        self.assertIsNone(iconpacks.image("nopack:house", 100, (255, 0, 0)))

    def test_pixel_stays_crisp(self):
        im = iconpacks.image("pixel:home", 144, (0, 255, 0))
        self.assertEqual(im.size, (144, 144))
        self.assertTrue(set(a for *_, a in list(im.getdata())) <= {0, 255})

    def test_theme_refs_follow_the_theme(self):
        self.assertEqual(iconpacks.resolve("theme:house", None), "outline:house")
        self.assertEqual(iconpacks.resolve("theme:house", {"icon_pack": "neon"}), "neon:house")
        self.assertEqual(iconpacks.resolve("theme:house", {"icon_pack": "missing"}), "outline:house")
        self.assertEqual(iconpacks.resolve("theme:gamepad", {"icon_pack": "neon"}), "pixel:gamepad")  # only pixel has it
        self.assertEqual(iconpacks.resolve("outline:house", {"icon_pack": "neon"}), "outline:house")

    def test_render_key_draws_icon(self):
        blank = render.render_key("modern", {"label": "x"})
        withicon = render.render_key("modern", {"icon": "filled:house", "label": "x"})
        self.assertNotEqual(blank.tobytes(), withicon.tobytes())
        unknown = render.render_key("modern", {"icon": "filled:nope", "label": "x"})
        self.assertEqual(blank.tobytes(), unknown.tobytes())  # unknown icons draw nothing, not the text "filled:nope"
        for t in render.list_themes():
            render.render_key(t["id"], {"icon": "theme:house", "label": "Home"}, {"text": "21", "icon": "theme:sun"})

    def test_schema_accepts_refs(self):
        schema.validate_key({"icon": "outline:music-notes"}, "k")
        schema.validate_key({"icon": "theme:house"}, "k")
        schema.validate_key({"icon": "♪"}, "k")
        with self.assertRaises(schema.ConfigError):
            schema.validate_key({"icon": "this is far too long"}, "k")

    def test_sprite_and_index(self):
        ix = iconpacks.index("pixel")
        sp = Image.open(io.BytesIO(iconpacks.sprite("pixel")))
        self.assertEqual(sp.mode, "RGBA")
        self.assertGreaterEqual(sp.width, ix["cols"] * ix["cell"])
        self.assertIn("home", ix["icons"])

    def test_renderer_cache_sees_theme_pack(self):
        r = render.Renderer()
        a = r.signature("modern", {"icon": "theme:house"}, {}, 1, 5, 3, 0)[0]
        b = r.signature("modern", {"icon": "theme:music"}, {}, 1, 5, 3, 0)[0]
        self.assertNotEqual(a, b)


class TestImport(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="vsdpad-icons-")
        iconpacks.set_user_dir(self.d)

    def tearDown(self):
        iconpacks.set_user_dir(None)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_png_and_svg(self):
        z = make_zip({"icons/Star Big.png": png(), "deep/folder/../moon.svg": SVG, "readme.txt": b"hi", "../evil.png": png()})
        r = iconpacks.import_zip(z, name="My Icons")
        self.assertEqual(r["id"], "my-icons")
        self.assertEqual(r["pending"], ["moon"])
        self.assertEqual(sorted(iconpacks.index("my-icons")["icons"]), ["evil", "star-big"])
        for root, _, files in os.walk(self.d):  # nothing escaped the pack folder
            self.assertTrue(os.path.abspath(root).startswith(os.path.abspath(self.d)))
        self.assertIn(b"<svg", iconpacks.pending_svg("my-icons", "moon"))
        iconpacks.add_drawn("my-icons", "moon", png())
        ix = iconpacks.index("my-icons")
        self.assertIn("moon", ix["icons"])
        self.assertEqual(ix["pending"], [])
        with self.assertRaises(ValueError):
            iconpacks.add_drawn("my-icons", "moon", png())  # only pending ones
        im = iconpacks.image("my-icons:star-big", 80, (0, 0, 255))
        self.assertEqual(im.getpixel((40, 40)), (0, 0, 255, 255))
        self.assertEqual(im.getpixel((1, 1))[3], 0)
        self.assertIn("my-icons", [p["id"] for p in iconpacks.list_packs()])
        exported = zipfile.ZipFile(io.BytesIO(iconpacks.export_zip("my-icons"))).namelist()
        self.assertIn("pack.json", exported)

    def test_opaque_png_uses_shape(self):
        iconpacks.import_zip(make_zip({"dot.png": png(color=(0, 0, 0, 255), bg=(255, 255, 255, 255))}), name="flat")
        m = iconpacks.image("flat:dot", 64, (255, 0, 0))
        self.assertGreater(m.getpixel((32, 32))[3], 200)
        self.assertLess(m.getpixel((1, 1))[3], 20)

    def test_keep_colours(self):
        iconpacks.import_zip(make_zip({"logo.png": png(color=(10, 200, 30, 255))}), name="logos", tint=False)
        im = iconpacks.image("logos:logo", 64, (255, 0, 0))
        self.assertEqual(im.getpixel((32, 32))[:3], (10, 200, 30))

    def test_rejects(self):
        with self.assertRaises(ValueError):
            iconpacks.import_zip(b"not a zip")
        with self.assertRaises(ValueError):
            iconpacks.import_zip(make_zip({"a.txt": b"x"}))
        with self.assertRaises(ValueError):
            iconpacks.import_zip(make_zip({"a.png": b"not really"}))
        with self.assertRaises(ValueError):
            iconpacks.import_zip(make_zip({"a.svg": b"<html>"}))
        with self.assertRaises(ValueError):
            iconpacks.import_zip(make_zip({"a.png": png()}), name="outline")  # built-in name
        with self.assertRaises(ValueError):
            iconpacks.import_zip(make_zip({"a.png": png()}), name="theme")
        iconpacks.import_zip(make_zip({"a.png": png()}), name="mine")
        with self.assertRaises(ValueError):
            iconpacks.import_zip(make_zip({"a.png": png()}), name="mine")
        iconpacks.import_zip(make_zip({"b.png": png()}), name="mine", replace=True)
        self.assertEqual(iconpacks.index("mine")["icons"], ["b"])
        with self.assertRaises(ValueError):
            iconpacks.delete("outline")
        with self.assertRaises(ValueError):
            iconpacks.pending_svg("mine", "../../x")
        iconpacks.delete("mine")
        self.assertIsNone(iconpacks.load("mine"))


if __name__ == "__main__":
    unittest.main()
