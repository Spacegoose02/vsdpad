"""Drawing keys. Everything is drawn at 216x216 and scaled by the device driver (72 px M18, 96 px XL, ...).

A theme is themes/<id>/pack.json (+ optional wallpaper.png sliced across the whole grid), or the built-in
"modern" look. A key can show: an icon glyph, a label, a picture (image), and live content from a widget
(big text, a small line under it, a progress bar, on/off dimming, a colour).
"""
import functools, json, os, re, threading

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import PKG_DIR

S = 216                 # render size
GAP = 48                # gap between keys in wallpaper pixels (cropped out so the scene looks continuous)
THEMES_DIR = os.path.join(PKG_DIR, "themes")
ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MODERN = {"id": "modern", "name": "Modern", "description": "Dark rounded tiles with a coloured outline.", "category": "Clean",
          "builtin": True}
DEFAULT_COLOR = (90, 140, 255)
SYMBOL_FONTS = ("seguisym.ttf", "seguiemj.ttf", "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")
TEXT_FONTS = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf")


@functools.lru_cache(maxsize=256)
def font(size, names=SYMBOL_FONTS):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def fit_font(text, start, minimum, maxw, names=SYMBOL_FONTS, step=2):
    size = start
    while size > minimum and font(size, names).getlength(text) > maxw:
        size -= step
    return font(size, names)


# ---- themes --------------------------------------------------------------------------------
def list_themes():
    out = [dict(MODERN)]
    if os.path.isdir(THEMES_DIR):
        for name in sorted(os.listdir(THEMES_DIR)):
            t = load_theme(name)
            if t:
                out.append({"id": t["id"], "name": t.get("name", name), "description": t.get("description", ""),
                            "author": t.get("author", ""), "category": t.get("category") or "Other", "builtin": False})
    return out


def load_theme(tid):
    if not tid or tid == "modern" or not ID_RE.match(tid):
        return None
    d = os.path.join(THEMES_DIR, tid)
    try:
        with open(os.path.join(d, "pack.json"), encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(t, dict) or t.get("id") != tid:
        return None
    t["dir"] = d
    return t


@functools.lru_cache(maxsize=8)
def _wallpaper(path, mtime, cols, rows):
    size = (cols * S + (cols - 1) * GAP, rows * S + (rows - 1) * GAP)
    return ImageOps.fit(Image.open(path).convert("RGB"), size, Image.LANCZOS)


def _tile(theme, slot, cols, rows, dy):
    wp = theme.get("wallpaper")
    if wp:
        path = os.path.join(theme["dir"], os.path.basename(wp))
        try:
            img = _wallpaper(path, os.path.getmtime(path), cols, rows)
            row, col = divmod(slot - 1, cols)
            x, y = col * (S + GAP), row * (S + GAP) - int(round(dy))
            return img.crop((x, y, x + S, y + S)).convert("RGBA")
        except OSError:
            pass
    bg = tuple(theme.get("style", {}).get("background", (12, 12, 16)))
    return Image.new("RGBA", (S, S), bg + (255,))


# ---- drawing helpers -----------------------------------------------------------------------
def _dim(c, f=0.35):
    return tuple(int(v * f) for v in c)


def _load_picture(path):
    try:
        with Image.open(path) as im:
            im.seek(0)
            return ImageOps.fit(im.convert("RGBA"), (S, S), Image.LANCZOS, centering=(0.5, 0.35))
    except (OSError, ValueError):
        return None


def _draw_content(img, key, live, color, label_color, label_fonts, glow, dy, maxw):
    """Glyph / big text / label / progress on top of a background. Returns the image."""
    has_text = bool(live.get("text"))
    glyph = live.get("icon", key.get("icon", "")) or ""
    label = live.get("label", key.get("label", "")) or ""
    sub = live.get("sub", "")
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if has_text:
        if glyph:
            d.text((S / 2, S * 0.2 + dy), glyph, fill=color + (255,), font=fit_font(glyph, 40, 20, maxw), anchor="mm")
        y = S * (0.47 if glyph else 0.42 if label else 0.5) + dy
        d.text((S / 2, y), live["text"], fill=color + (255,), font=fit_font(live["text"], 76, 26, maxw + 20, TEXT_FONTS), anchor="mm")
        if sub:
            d.text((S / 2, y + 44), sub, fill=color + (255,), font=font(26, TEXT_FONTS), anchor="mm")
    elif glyph:
        d.text((S / 2, S * (0.38 if label else 0.5) + dy), glyph, fill=color + (255,),
               font=fit_font(glyph, 116, 40, maxw, step=8), anchor="mm")
    if glow:
        for r in (glow * 2, glow):
            img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(r)))
    img = Image.alpha_composite(img, layer)
    if label and not (has_text and sub):
        ImageDraw.Draw(img).text((S / 2, S * (0.78 if (glyph or has_text) else 0.5) + dy), label, fill=label_color,
                                 font=fit_font(label, 40, 18, maxw, label_fonts), anchor="mm",
                                 stroke_width=4, stroke_fill=(0, 0, 0))
    p = live.get("progress")
    if isinstance(p, (int, float)):
        d2 = ImageDraw.Draw(img)
        x0, x1, y = 40, S - 40, S - 34 + dy
        d2.rounded_rectangle((x0, y, x1, y + 8), 4, fill=(255, 255, 255, 50))
        if p > 0:
            d2.rounded_rectangle((x0, y, x0 + max(8, (x1 - x0) * min(1, p)), y + 8), 4, fill=color + (255,))
    return img


def _modern(key, live, slot, dy, picture):
    on = live.get("on", True)
    color = tuple(live.get("color") or key.get("color") or DEFAULT_COLOR)
    if not on:
        color = _dim(color)
    img = Image.new("RGBA", (S, S), (18, 18, 22, 255))
    d = ImageDraw.Draw(img)
    box = (11, 11 + dy, S - 12, S - 12 + dy)
    if picture is not None:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, 28, fill=255)
        pic = picture if on else Image.blend(picture, Image.new("RGBA", (S, S), (0, 0, 0, 255)), 0.6)
        img.paste(pic, (0, int(dy)), mask)
        d.rounded_rectangle(box, 28, outline=color, width=8)
    else:
        d.rounded_rectangle(box, 28, fill=(34, 36, 44), outline=color, width=10)
    content = dict(key)
    if picture is not None:
        content.pop("icon", None)
    return _draw_content(img, content, live, color, (235, 235, 240) if on else (120, 120, 125), TEXT_FONTS, 0, dy, S - 56)


def _themed(theme, key, live, slot, cols, rows, dy, picture):
    st = theme.get("style", {})
    on = live.get("on", True)
    has = bool(key.get("icon") or key.get("label") or live or picture is not None)
    img = picture.copy() if picture is not None else _tile(theme, slot, cols, rows, dy)
    img = Image.alpha_composite(img, Image.new("RGBA", (S, S), (0, 0, 0, int(255 * st.get("dim", 0.3)))))
    colors = st.get("icon_colors")
    color = tuple(live.get("color") or (colors[(slot - 1) % len(colors)] if colors else key.get("color") or DEFAULT_COLOR))
    if not on:
        color = _dim(color)
        img = Image.alpha_composite(img, Image.new("RGBA", (S, S), (0, 0, 0, 120)))
    if has:
        content = dict(key)
        if picture is not None:
            content.pop("icon", None)
        lc = tuple(st.get("label_color", (240, 240, 240)))
        img = _draw_content(img, content, live, color, lc if on else _dim(lc, 0.5),
                            tuple(st.get("label_fonts", TEXT_FONTS)) + TEXT_FONTS, st.get("glow", 8), dy, S - 76)
    frame = st.get("frame")
    if frame in ("brackets", "outline", "pixel"):
        layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d, oy = ImageDraw.Draw(layer), int(round(dy))
        c = color + (230 if has else 90,)
        if frame == "brackets":
            n, w, m = 36, 7, 28  # inset: the M18 bezel hides the outer ~4-5 px
            for (x, y, sx, sy) in ((m, m, 1, 1), (S - m, m, -1, 1), (m, S - m, 1, -1), (S - m, S - m, -1, -1)):
                y += oy
                d.line((x, y, x + sx * n, y), fill=c, width=w)
                d.line((x, y, x, y + sy * n), fill=c, width=w)
        elif frame == "outline":  # one rounded border around the key
            m = 14
            d.rounded_rectangle((m, m + oy, S - m, S - m + oy), int(st.get("frame_radius", 22)), outline=c,
                                width=int(st.get("frame_width", 6)))
        else:  # "pixel": chunky 8-bit border with stepped corners
            b, m = 12, 16
            lo, hi = m, S - m - b
            for x0, y0, x1, y1 in ((lo + b, lo, hi, lo + b), (lo + b, hi, hi, hi + b),        # top, bottom
                                   (lo, lo + b, lo + b, hi), (hi, lo + b, hi + b, hi),        # left, right
                                   (lo + b, lo + b, lo + 2 * b, lo + 2 * b), (hi - b, lo + b, hi, lo + 2 * b),
                                   (lo + b, hi - b, lo + 2 * b, hi), (hi - b, hi - b, hi, hi)):  # inner corner steps
                d.rectangle((x0, y0 + oy, x1 - 1, y1 - 1 + oy), fill=c)
        img = Image.alpha_composite(img, layer)
    return img


BLANK = None


def render_key(theme_id, key, live=None, slot=1, cols=5, rows=3, dy=0, picture_path=None):
    """The 216x216 RGB picture for one key, or None for a blank key on the modern theme (drawn black)."""
    key, live = key or {}, live or {}
    theme = load_theme(theme_id)
    picture = _load_picture(picture_path) if picture_path else None
    dy = dy * S / 72  # offsets are in M18 key pixels
    if theme is None:
        if not (key.get("icon") or key.get("label") or live or picture is not None):
            return None
        return _modern(key, live, slot, dy, picture).convert("RGB")
    return _themed(theme, key, live, slot, cols, rows, dy, picture).convert("RGB")


def black():
    return Image.new("RGB", (S, S), (0, 0, 0))


class Renderer:
    """Caches rendered keys by their inputs, so redrawing an unchanged key costs nothing."""

    def __init__(self, image_path=lambda ref: None, size=512):
        self.image_path = image_path
        self._cache, self._lock, self.size = {}, threading.Lock(), size

    def signature(self, theme_id, key, live, slot, cols, rows, dy):
        ref = (key or {}).get("image")
        path = self.image_path(ref) if ref else None
        try:
            mtime = os.path.getmtime(path) if path else 0
        except OSError:
            path, mtime = None, 0
        visual = {k: v for k, v in (key or {}).items() if k in ("icon", "label", "color")}
        theme = load_theme(theme_id)
        if theme and theme.get("wallpaper"):
            pos = (slot, cols, rows)  # wallpaper slices depend on the position
        elif theme and theme.get("style", {}).get("icon_colors"):
            pos = (slot % len(theme["style"]["icon_colors"]),)
        else:
            pos = ()
        return json.dumps([theme_id, visual, live or {}, pos, dy, path, mtime], sort_keys=True, ensure_ascii=False), path

    def render(self, theme_id, key, live, slot, cols, rows, dy=0):
        """Return (signature, image or None)."""
        sig, path = self.signature(theme_id, key, live, slot, cols, rows, dy)
        with self._lock:
            if sig in self._cache:
                return sig, self._cache[sig]
        img = render_key(theme_id, key, live, slot, cols, rows, dy, path)
        with self._lock:
            if len(self._cache) >= self.size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[sig] = img
        return sig, img

    def grid(self, theme_id, keys, cols, rows, live=None, offsets=(), tile=108, gap=12):
        """Preview of a whole page."""
        out = Image.new("RGB", (cols * tile + (cols - 1) * gap, rows * tile + (rows - 1) * gap), (8, 8, 10))
        for slot in range(1, cols * rows + 1):
            r = (slot - 1) // cols
            dy = offsets[r] if r < len(offsets) else 0
            _, img = self.render(theme_id, keys.get(str(slot)), (live or {}).get(str(slot)), slot, cols, rows, dy)
            if img is not None:
                c = (slot - 1) % cols
                out.paste(img.resize((tile, tile), Image.LANCZOS), (c * (tile + gap), r * (tile + gap)))
        return out
