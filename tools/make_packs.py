"""Draws the two built-in wallpaper packs (original art, procedural). Run: python tools/make_packs.py
Output: icons/cyberpunk/{pack.json,wallpaper.png} and icons/nuclear/{pack.json,wallpaper.png}
"""
import json, math, os, random, sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from iconpacks import WALL_SIZE  # noqa: E402

W, H = WALL_SIZE
HORIZON = int(H * 0.87)  # low horizon: the city fills all three key rows, street/ground is a thin strip


def vgrad(stops, w=W, h=H):
    """Vertical gradient from [(pos 0-1, (r,g,b)), ...]."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                f = (t - p0) / (p1 - p0) if p1 > p0 else 0
                d.line((0, y, w, y), fill=tuple(int(c0[i] + (c1[i] - c0[i]) * f) for i in range(3)))
                break
    return img


def add(base, layer):
    """Additive (screen-like) blend of an RGB layer onto base."""
    return ImageChops.add(base, layer)


def glow(layer, radius, strength=1.0):
    g = layer.filter(ImageFilter.GaussianBlur(radius))
    if strength != 1.0:
        g = g.point(lambda v: min(255, int(v * strength)))
    return g


def window_dots(d, rnd, x0, y0, x1, y1, colors, p):
    for wy in range(y0 + 10, y1 - 8, 14):
        for wx in range(x0 + 8, x1 - 8, 12):
            if rnd.random() < p:
                d.rectangle((wx, wy, wx + 4, wy + 6), fill=rnd.choice(colors))


# ----------------------------------------------------------------------------- cyberpunk
def cyberpunk():
    rnd = random.Random(2077)
    sky = vgrad([(0, (6, 3, 24)), (0.35, (34, 8, 78)), (0.58, (120, 16, 130)), (0.68, (255, 48, 150)),
                 (0.70, (255, 120, 190)), (1, (6, 4, 18))])
    img = sky.copy()
    # soft pink haze at the horizon
    haze = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(haze).ellipse((-W * 0.2, HORIZON - 150, W * 1.2, HORIZON + 90), fill=(150, 20, 100))
    img = add(img, glow(haze, 70))

    # three skyline layers (far -> near)
    neon = Image.new("RGB", (W, H), (0, 0, 0))
    nd = ImageDraw.Draw(neon)
    for layer, (col, hmin, hmax, wmin, wmax, lit) in enumerate([
            ((46, 14, 84), 200, 430, 26, 64, 0.14),
            ((24, 10, 52), 320, 580, 46, 100, 0.26),
            ((10, 6, 28), 430, 700, 70, 150, 0.34)]):
        d = ImageDraw.Draw(img)
        x = -30
        while x < W:
            bw, bh = rnd.randint(wmin, wmax), rnd.randint(hmin, hmax)
            top = HORIZON - bh
            d.rectangle((x, top, x + bw, HORIZON + 4), fill=col)
            if layer >= 1 and rnd.random() < 0.5:      # antenna / stepped roof
                d.rectangle((x + bw // 3, top - 28, x + bw // 3 + 5, top), fill=col)
                d.rectangle((x + 8, top - 14, x + bw - 8, top), fill=col)
            window_dots(d, rnd, x, top, x + bw, HORIZON,
                        [(0, 230, 255), (255, 60, 190), (255, 190, 70), (190, 120, 255)], lit)
            if layer >= 1 and rnd.random() < 0.55:     # neon trim / sign
                c = rnd.choice([(0, 240, 255), (255, 50, 190), (255, 230, 60)])
                if rnd.random() < 0.5:
                    nd.rectangle((x, top, x + 5, top + rnd.randint(60, 220)), fill=c)
                else:
                    sy = top + rnd.randint(20, bh // 2)
                    nd.rectangle((x + 10, sy, x + bw - 10, sy + 18), outline=c, width=4)
                    nd.line((x + 20, sy + 9, x + bw - 20, sy + 9), fill=c, width=3)
            x += bw + rnd.randint(-6, 8)
    img = add(img, glow(neon, 10, 1.6))
    img = add(img, neon)

    # wet street with reflection of the skyline
    street = vgrad([(0, (22, 8, 48)), (1, (4, 2, 12))], h=H - HORIZON)
    refl = img.crop((0, HORIZON - (H - HORIZON), W, HORIZON)).transpose(Image.FLIP_TOP_BOTTOM)
    refl = refl.filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.45))
    street = add(street, refl)
    for _ in range(26):                                   # neon streaks on the wet road
        sx, c = rnd.randint(0, W), rnd.choice([(0, 200, 255), (255, 40, 170)])
        ImageDraw.Draw(street).line((sx, 0, sx + rnd.randint(-30, 30), H - HORIZON), fill=tuple(v // 3 for v in c), width=rnd.randint(2, 6))
    img.paste(street, (0, HORIZON))
    ImageDraw.Draw(img).line((0, HORIZON, W, HORIZON), fill=(255, 150, 220), width=2)

    # rain
    rain = Image.new("RGB", (W, H), (0, 0, 0))
    rd = ImageDraw.Draw(rain)
    for _ in range(1100):
        x, y, ln = rnd.randint(-40, W), rnd.randint(0, H), rnd.randint(14, 34)
        v = rnd.randint(40, 110)
        rd.line((x, y, x - ln // 4, y + ln), fill=(v, v + 10, v + 30), width=1)
    img = add(img, rain)
    return img


# ----------------------------------------------------------------------------- nuclear
def trefoil(draw, cx, cy, r, color):
    """Radiation symbol: three 60-degree blades, centre disc."""
    for a in (-90, 30, 150):  # PIL angles run clockwise from 3 o'clock: top, lower-right, lower-left
        draw.pieslice((cx - r, cy - r, cx + r, cy + r), a - 30, a + 30, fill=color)
    draw.ellipse((cx - r * 0.38, cy - r * 0.38, cx + r * 0.38, cy + r * 0.38), fill=(0, 0, 0))
    draw.ellipse((cx - r * 0.24, cy - r * 0.24, cx + r * 0.24, cy + r * 0.24), fill=color)


def nuclear():
    rnd = random.Random(1945)
    img = vgrad([(0, (34, 26, 14)), (0.22, (110, 66, 20)), (0.45, (214, 118, 28)), (0.62, (255, 168, 56)),
                 (0.70, (255, 214, 120)), (1, (10, 8, 6))])
    # drifting dark smoke bands
    smoke = Image.new("RGB", (W, H), (0, 0, 0))
    sd = ImageDraw.Draw(smoke)
    for _ in range(14):
        cx, cy = rnd.randint(0, W), rnd.randint(0, int(H * 0.45))
        sd.ellipse((cx - rnd.randint(120, 320), cy - rnd.randint(20, 60), cx + rnd.randint(120, 320), cy + rnd.randint(20, 60)),
                   fill=(26, 22, 14))
    smoke = smoke.filter(ImageFilter.GaussianBlur(26))
    img = ImageChops.blend(img, ImageChops.multiply(img, ImageChops.invert(smoke.point(lambda v: min(255, v * 5)))), 0.45)

    # huge pale sun with a faint radiation trefoil
    sun = Image.new("RGB", (W, H), (0, 0, 0))
    sx, sy = int(W * 0.80), int(H * 0.20)
    ImageDraw.Draw(sun).ellipse((sx - 110, sy - 110, sx + 110, sy + 110), fill=(120, 92, 40))
    img = add(img, glow(sun, 40))
    tre = Image.new("RGB", (W, H), (0, 0, 0))
    trefoil(ImageDraw.Draw(tre), sx, sy, 84, (255, 200, 80))
    img = add(img, tre.point(lambda v: int(v * 0.55)))

    # distant mushroom cloud, lit orange from below
    cloud = Image.new("L", (W, H), 0)
    cd = ImageDraw.Draw(cloud)
    mx, base, top = int(W * 0.34), HORIZON, int(H * 0.04)
    cd.polygon([(mx - 34, base), (mx + 34, base), (mx + 20, top + 220), (mx - 20, top + 220)], fill=255)   # stem
    cd.ellipse((mx - 150, top + 130, mx + 150, top + 250), fill=255)                                         # skirt
    for ox, oy, rr in ((-95, 100, 80), (95, 100, 80), (0, 70, 115), (-55, 30, 82), (55, 30, 82), (0, 0, 84)):
        cd.ellipse((mx + ox - rr, top + 110 + oy - rr, mx + ox + rr, top + 110 + oy + rr), fill=255)     # cap
    cloud = cloud.filter(ImageFilter.GaussianBlur(3))
    lit = vgrad([(0, (255, 200, 120)), (0.55, (200, 90, 30)), (1, (70, 30, 14))])
    img = Image.composite(lit, img, cloud.point(lambda v: int(v * 0.85)))
    img = add(img, glow(Image.composite(Image.new("RGB", (W, H), (200, 80, 20)), Image.new("RGB", (W, H), (0, 0, 0)), cloud), 24, 0.7))

    # ruined skyline: jagged broken tops
    d = ImageDraw.Draw(img)
    for col, hmin, hmax, wmin, wmax in (((122, 70, 28), 140, 300, 30, 70), ((62, 38, 18), 220, 440, 50, 110), ((16, 11, 8), 320, 600, 70, 150)):
        x = -30
        while x < W:
            bw, bh = rnd.randint(wmin, wmax), rnd.randint(hmin, hmax)
            top = HORIZON - bh
            pts = [(x, HORIZON + 4), (x, top + rnd.randint(0, 30))]
            n = max(3, bw // 14)
            for i in range(1, n + 1):                      # broken, uneven roofline
                pts.append((x + bw * i // n, top + rnd.randint(-8, 55) * (1 if rnd.random() < 0.6 else 2)))
            pts.append((x + bw, HORIZON + 4))
            d.polygon(pts, fill=col)
            if col == (16, 11, 8):                         # a few dim orange windows in the near layer
                for _ in range(bw * bh // 1800):
                    wx, wy = x + rnd.randint(6, max(7, bw - 10)), top + rnd.randint(40, max(41, bh - 10))
                    d.rectangle((wx, wy, wx + 4, wy + 6), fill=(150, 80, 20))
            x += bw + rnd.randint(-6, 40)                  # gaps let the sky and cloud show through

    # ground with glowing green cracks and radioactive puddles
    ground = vgrad([(0, (36, 28, 18)), (1, (6, 5, 4))], h=H - HORIZON)
    img.paste(ground, (0, HORIZON))
    cracks = Image.new("RGB", (W, H), (0, 0, 0))
    kd = ImageDraw.Draw(cracks)
    for _ in range(34):
        x, y = rnd.randint(0, W), HORIZON + rnd.randint(8, H - HORIZON)
        pts = [(x, y)]
        for _ in range(rnd.randint(3, 7)):
            x += rnd.randint(-70, 70)
            y += rnd.randint(-18, 26)
            pts.append((x, min(H - 1, max(HORIZON + 4, y))))
        kd.line(pts, fill=(60, 255, 70), width=rnd.randint(2, 4))
    for _ in range(6):
        px, py = rnd.randint(40, W - 40), rnd.randint(HORIZON + 40, H - 40)
        kd.ellipse((px - 70, py - 9, px + 70, py + 9), fill=(40, 200, 60))
    img = add(img, glow(cracks, 9, 1.7))
    img = add(img, cracks.point(lambda v: v // 2))
    ImageDraw.Draw(img).line((0, HORIZON, W, HORIZON), fill=(255, 200, 100), width=2)

    # drifting ash and embers
    emb = Image.new("RGB", (W, H), (0, 0, 0))
    ed = ImageDraw.Draw(emb)
    for _ in range(320):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.choice((1, 1, 2, 3))
        ed.ellipse((x - r, y - r, x + r, y + r), fill=rnd.choice([(255, 150, 40), (200, 200, 190), (255, 90, 20)]))
    img = add(img, add(emb.filter(ImageFilter.GaussianBlur(1)), emb.point(lambda v: v // 3)))

    # hazard stripes along the very bottom edge
    d = ImageDraw.Draw(img)
    sh = 26
    d.rectangle((0, H - sh, W, H), fill=(14, 12, 8))
    for x in range(-sh, W + sh, 44):
        d.polygon([(x, H), (x + 22, H), (x + 22 + sh, H - sh), (x + sh, H - sh)], fill=(235, 180, 20))
    return img


PACKS = {
    "cyberpunk": (cyberpunk, {
        "id": "cyberpunk", "name": "Cyberpunk City", "author": "vsdpad",
        "description": "Neon skyline in the rain: magenta haze, cyan signs, wet streets.",
        "wallpaper": "wallpaper.png",
        "style": {"dim": 0.16, "glow": 9, "icon_colors": [[0, 240, 255], [255, 60, 190]], "label_color": [235, 245, 255],
                  "label_fonts": ["bahnschrift.ttf", "consolab.ttf", "segoeuib.ttf"], "frame": "brackets"}}),
    "nuclear": (nuclear, {
        "id": "nuclear", "name": "Nuclear City", "author": "vsdpad",
        "description": "Ruined skyline under an orange sky: mushroom cloud, ash, toxic-green cracks.",
        "wallpaper": "wallpaper.png",
        "style": {"dim": 0.20, "glow": 8, "icon_colors": [[255, 184, 0], [130, 255, 70]], "label_color": [255, 238, 205],
                  "label_fonts": ["bahnschrift.ttf", "impact.ttf", "segoeuib.ttf"], "frame": "brackets"}}),
}

if __name__ == "__main__":
    for pid, (draw, meta) in PACKS.items():
        out = os.path.join(ROOT, "icons", pid)
        os.makedirs(out, exist_ok=True)
        draw().save(os.path.join(out, "wallpaper.png"), optimize=True)
        with open(os.path.join(out, "pack.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        print("wrote", out)
