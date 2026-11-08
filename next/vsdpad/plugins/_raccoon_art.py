"""Raccoon mascot art for the Raccoon plugin: every mood is drawn here with PIL (4x supersampled), in the flat
style of the Den Duty / Raccboard app icons. No picture files to ship."""
import math
from PIL import Image, ImageDraw, ImageFont

S = 216
K = 4                      # supersampling
W = S * K
GREY, GREY_D, DARK, WHITE, MASK = (150, 155, 165), (110, 114, 124), (40, 42, 48), (245, 245, 245), (28, 30, 36)
BG = {"idle": ((40, 170, 150), (20, 110, 100)), "music": ((150, 70, 220), (70, 30, 130)), "live": ((235, 60, 70), (120, 20, 40)),
      "night": ((30, 40, 90), (8, 10, 30)), "happy": ((255, 140, 180), (200, 70, 130))}


def _bg(mood, t):
    a, b = BG[mood]
    im = Image.new("RGB", (W, W), b)
    d = ImageDraw.Draw(im)
    steps = 40
    for i in range(steps, 0, -1):
        f = i / steps
        c = tuple(int(b[j] + (a[j] - b[j]) * (1 - f)) for j in range(3))
        r = W * 0.75 * f
        d.ellipse((W / 2 - r, W * 0.45 - r, W / 2 + r, W * 0.45 + r), fill=c)
    return im.convert("RGBA")


def _p(x, y, cx, cy, s, rot):
    """Map head-space point (x, y in -1..1) to canvas px, with scale and rotation about the head centre."""
    x, y = x * s, y * s
    c, si = math.cos(rot), math.sin(rot)
    return cx + x * c - y * si, cy + x * si + y * c


def _poly(d, pts, fill, cx, cy, s, rot):
    d.polygon([_p(x, y, cx, cy, s, rot) for x, y in pts], fill=fill)


def _ell(d, x, y, rx, ry, fill, cx, cy, s, rot, n=36):
    pts = [(x + rx * math.cos(2 * math.pi * i / n), y + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]
    _poly(d, pts, fill, cx, cy, s, rot)


def _arc(d, x, y, rx, ry, a0, a1, width, fill, cx, cy, s, rot, n=20):
    pts = [_p(x + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)), y + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
              cx, cy, s, rot) for i in range(n + 1)]
    d.line(pts, fill=fill, width=int(width * K), joint="curve")


def raccoon(img, cx, cy, s, rot=0.0, eyes="open", look=0.0, mouth="smile", headphones=False, blush=False):
    d = ImageDraw.Draw(img)
    a = (cx, cy, s, rot)
    # ears
    for sx in (-1, 1):
        _poly(d, [(sx * 0.55, -0.55), (sx * 0.95, -1.05), (sx * 0.98, -0.35)], GREY_D, *a)
        _poly(d, [(sx * 0.62, -0.55), (sx * 0.88, -0.9), (sx * 0.9, -0.45)], DARK, *a)
    # head
    _ell(d, 0, 0, 1.0, 0.86, GREY, *a)
    # forehead stripe
    _poly(d, [(-0.12, -0.86), (0.12, -0.86), (0.07, -0.35), (-0.07, -0.35)], GREY_D, *a)
    # white cheeks / muzzle
    _ell(d, 0, 0.32, 0.78, 0.5, WHITE, *a)
    _ell(d, -0.55, 0.05, 0.3, 0.28, WHITE, *a)
    _ell(d, 0.55, 0.05, 0.3, 0.28, WHITE, *a)
    # mask band
    _poly(d, [(-0.98, -0.1), (-0.6, -0.36), (-0.15, -0.26), (0, -0.18), (0.15, -0.26), (0.6, -0.36), (0.98, -0.1),
              (0.85, 0.12), (0.45, 0.1), (0.15, 0.02), (0, 0.06), (-0.15, 0.02), (-0.45, 0.1), (-0.85, 0.12)], MASK, *a)
    # eyes
    for sx in (-1, 1):
        ex, ey = sx * 0.42, -0.1
        if eyes == "open":
            _ell(d, ex, ey, 0.15, 0.17, WHITE, *a)
            _ell(d, ex + look * 0.06, ey + 0.02, 0.09, 0.11, (20, 20, 24), *a)
            _ell(d, ex + look * 0.06 - 0.03, ey - 0.03, 0.035, 0.035, WHITE, *a)
        elif eyes == "closed":
            _arc(d, ex, ey - 0.02, 0.13, 0.08, 10, 170, 3.2, WHITE, *a)
        elif eyes == "happy":
            _arc(d, ex, ey + 0.06, 0.13, 0.12, 200, 340, 3.6, WHITE, *a)
        elif eyes == "wide":
            _ell(d, ex, ey, 0.18, 0.2, WHITE, *a)
            _ell(d, ex, ey + 0.02, 0.1, 0.12, (20, 20, 24), *a)
            _ell(d, ex - 0.035, ey - 0.04, 0.045, 0.045, WHITE, *a)
    if blush:
        for sx in (-1, 1):
            _ell(d, sx * 0.58, 0.28, 0.12, 0.07, (255, 150, 170), *a)
    # nose and mouth
    _ell(d, 0, 0.22, 0.13, 0.09, (25, 25, 30), *a)
    if mouth == "smile":
        _arc(d, -0.09, 0.32, 0.09, 0.09, 10, 170, 2.4, (25, 25, 30), *a)
        _arc(d, 0.09, 0.32, 0.09, 0.09, 10, 170, 2.4, (25, 25, 30), *a)
    elif mouth == "open":
        _ell(d, 0, 0.45, 0.14, 0.12, (60, 20, 30), *a)
        _ell(d, 0, 0.5, 0.08, 0.05, (240, 110, 130), *a)
    elif mouth == "small":
        _arc(d, 0, 0.36, 0.05, 0.03, 20, 160, 2.2, (25, 25, 30), *a)
    if headphones:
        _arc(d, 0, -0.1, 1.08, 1.0, 195, 345, 7, (40, 30, 70), *a)
        for sx in (-1, 1):
            _ell(d, sx * 1.0, 0.05, 0.2, 0.3, (60, 40, 110), *a)
            _ell(d, sx * 1.02, 0.05, 0.12, 0.2, (0, 230, 255), *a)


def _font(size):
    for name in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _note(d, x, y, sz, col):
    d.ellipse((x - sz, y - sz * 0.7, x + sz, y + sz * 0.7), fill=col)
    d.line((x + sz * 0.9, y, x + sz * 0.9, y - sz * 3), fill=col, width=int(sz * 0.4))
    d.line((x + sz * 0.9, y - sz * 3, x + sz * 2, y - sz * 2.3), fill=col, width=int(sz * 0.4))


def _heart(d, x, y, s, col):
    d.ellipse((x - s, y - s, x, y), fill=col)
    d.ellipse((x, y - s, x + s, y), fill=col)
    d.polygon(((x - s * 0.98, y - s * 0.4), (x + s * 0.98, y - s * 0.4), (x, y + s * 0.95)), fill=col)


def _star(d, x, y, s, col):
    d.polygon([(x + s * (1 if i % 2 == 0 else 0.4) * math.cos(math.pi / 2 + i * math.pi / 5),
                y - s * (1 if i % 2 == 0 else 0.4) * math.sin(math.pi / 2 + i * math.pi / 5)) for i in range(10)], fill=col)


FRAMES = 8


def frame(mood, i):
    """Frame i (0..FRAMES-1) of a mood, as a 216x216 RGB picture."""
    t = i / FRAMES
    img = _bg(mood, t)
    d = ImageDraw.Draw(img)
    cx, cy, s = W / 2, W * 0.56, W * 0.33
    if mood == "idle":
        bob = math.sin(t * 2 * math.pi) * K * 2
        look = [0, 0, -1, -1, 0, 0, 1, 1][i]
        raccoon(img, cx, cy + bob, s, eyes="closed" if i == 4 else "open", look=look)
    elif mood == "music":
        rot = math.radians(7) * math.sin(t * 2 * math.pi * 2)
        raccoon(img, cx, cy + abs(math.sin(t * 2 * math.pi * 2)) * K * 4, s, rot, eyes="happy", headphones=True)
        for k, (x0, col) in enumerate(((0.18, (255, 230, 90)), (0.8, (120, 255, 230)), (0.5, (255, 140, 220)))):
            f = (t + k / 3) % 1
            _note(d, W * x0 + math.sin(f * 6) * K * 6, W * (0.35 - 0.3 * f), K * 6, col + (int(255 * (1 - f)),))
    elif mood == "live":
        raccoon(img, cx, cy + (K * 3 if i % 2 else 0), s * (1.02 if i % 2 else 1), eyes="wide", mouth="open")
        if i % 4 < 3:  # blinking LIVE badge
            bx, by = W * 0.5, W * 0.12
            d.rounded_rectangle((bx - K * 40, by - K * 13, bx + K * 40, by + K * 13), K * 8, fill=(255, 255, 255))
            d.ellipse((bx - K * 33, by - K * 6, bx - K * 21, by + K * 6), fill=(230, 30, 40))
            d.text((bx + K * 8, by), "LIVE", fill=(230, 30, 40), font=_font(K * 19), anchor="mm")
        for k in range(3):
            f = (t * 2 + k / 3) % 1
            _star(d, W * (0.15 + 0.35 * k), W * (0.35 + 0.08 * (k % 2)), K * 8 * math.sin(f * math.pi), (255, 240, 120))
    elif mood == "night":
        bob = math.sin(t * 2 * math.pi) * K * 3
        for x0, y0, r in ((0.4, 0.12, 3), (0.9, 0.12, 2), (0.6, 0.06, 2), (0.3, 0.3, 1.5)):
            _star(d, W * x0, W * y0, K * r * (1.6 if (i + int(x0 * 10)) % 4 == 0 else 1.1), (255, 250, 200))
        moon = Image.new("L", (W, W), 0)
        md = ImageDraw.Draw(moon)
        md.ellipse((W * 0.06, W * 0.06, W * 0.26, W * 0.26), fill=255)
        md.ellipse((W * 0.11, W * 0.03, W * 0.31, W * 0.23), fill=0)  # crescent: a disc with a bite out of it
        img.paste((255, 245, 200, 255), (0, 0), moon)
        raccoon(img, cx, cy + bob, s, math.radians(-8), eyes="closed", mouth="small")
        for k in range(3):
            f = (t + k / 3) % 1
            fnt = _font(int(K * (10 + 10 * f)))
            d.text((W * (0.72 + 0.12 * f), W * (0.42 - 0.3 * f)), "z", fill=(220, 230, 255, int(255 * (1 - f * 0.8))), font=fnt, anchor="mm")
    elif mood == "happy":
        raccoon(img, cx, cy - abs(math.sin(t * 2 * math.pi)) * K * 10, s, math.radians(5) * math.sin(t * 4 * math.pi),
                eyes="happy", mouth="open", blush=True)
        for k in range(4):
            f = (t + k / 4) % 1
            _heart(d, W * (0.12 + 0.25 * k), W * (0.38 - 0.3 * f), K * 9 * (0.6 + 0.4 * math.sin(f * math.pi)), (255, 70, 120))
    return img.resize((S, S), Image.LANCZOS).convert("RGB")


MOODS = tuple(BG)


def gif(mood, path):
    """Write a looping GIF of a mood (8 frames, 5 per second)."""
    frames = [frame(mood, i) for i in range(FRAMES)]
    frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], duration=200, loop=0, disposal=1)
