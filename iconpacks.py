"""Icon packs: themes that restyle the pad. See HANDOFF.md section 5.7.

A pack is a folder icons/<id>/ with pack.json and optionally wallpaper.png. The wallpaper is one
picture sliced across all 15 keys (with the physical gaps between keys cropped out so the scene
reads as continuous). Each key's glyph and label are drawn on top with a glow.
The built-in pack "modern" is the original dark rounded tile look (hardware.render).
"""
import functools, json, os, re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import hardware

HERE = os.path.dirname(os.path.abspath(__file__))
PACKS_DIR = os.path.join(HERE, "icons")
K, G = 216, 48          # key tile size and inter-key gap in wallpaper pixels
WALL_SIZE = (5 * K + 4 * G, 3 * K + 2 * G)  # (1272, 744)
ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MODERN = {"id": "modern", "name": "Modern", "description": "Dark rounded tiles with a coloured outline.", "builtin": True}


def list_packs():
    packs = [dict(MODERN)]
    if os.path.isdir(PACKS_DIR):
        for name in sorted(os.listdir(PACKS_DIR)):
            p = load_pack(name)
            if p and name != "modern":
                packs.append({"id": p["id"], "name": p.get("name", name), "description": p.get("description", ""),
                              "author": p.get("author", ""), "builtin": False})
    return packs


def load_pack(pid):
    """Return the pack dict (with 'dir'), or None for modern / missing / invalid packs."""
    if not pid or pid == "modern" or not ID_RE.match(pid):
        return None
    d = os.path.join(PACKS_DIR, pid)
    try:
        with open(os.path.join(d, "pack.json"), encoding="utf-8") as f:
            p = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(p, dict) or p.get("id") != pid:
        return None
    p["dir"] = d
    return p


@functools.lru_cache(maxsize=8)
def _wallpaper(path, mtime):
    return Image.open(path).convert("RGB").resize(WALL_SIZE, Image.LANCZOS)


def tile(pack, slot, dy=0):
    """The key-sized slice of the wallpaper (or a flat background) for a slot 1-15.
    dy > 0 moves the picture down on the key (the window slides up the wallpaper)."""
    wp = pack.get("wallpaper")
    if wp:
        path = os.path.join(pack["dir"], os.path.basename(wp))
        try:
            img = _wallpaper(path, os.path.getmtime(path))
            row, col = divmod(slot - 1, 5)
            x, y = col * (K + G), row * (K + G) - int(round(dy))
            return img.crop((x, y, x + K, y + K)).convert("RGBA")  # out-of-range rows come back black (hidden by the bezel)
        except OSError:
            pass
    return Image.new("RGBA", (K, K), tuple(pack.get("style", {}).get("background", (12, 12, 16))) + (255,))


def _font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _has_content(spec):
    return bool(spec.get("icon") or spec.get("label"))


def row_shift(cfg, slot):
    """Per-row nudge of the whole key picture in 72-px key pixels (config 'row_offset': [top, middle, bottom])."""
    offs = cfg.get("row_offset") or [0, 0, 0]
    return offs[(slot - 1) // 5] if 1 <= slot <= 15 and len(offs) == 3 else 0


def render_key(pack, slot, spec, dy=0):
    """Render one key for a pack: wallpaper tile + glowing glyph + outlined label + HUD brackets.
    dy shifts the whole picture down (216-px units): wallpaper, brackets, glyph and label together."""
    st = pack.get("style", {})
    img = tile(pack, slot, dy)
    has = _has_content(spec)
    # Same dark overlay on every key (filled or blank) so the whole pad reads as one theme.
    img = Image.alpha_composite(img, Image.new("RGBA", (K, K), (0, 0, 0, int(255 * st.get("dim", 0.3)))))
    colors = st.get("icon_colors")
    color = tuple(colors[(slot - 1) % len(colors)]) if colors else tuple(spec.get("color", (90, 140, 255)))
    maxw = K - 76  # stay inside the brackets
    glyph, label = spec.get("icon", ""), spec.get("label", "")
    if glyph and has:
        size = 116
        while size > 40 and hardware.font(size).getlength(glyph) > maxw:
            size -= 8
        layer = Image.new("RGBA", (K, K), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((K / 2, K * (0.38 if label else 0.5) + dy), glyph, fill=color + (255,),
                                   font=hardware.font(size), anchor="mm")
        glow = st.get("glow", 8)
        for r, boost in ((glow * 2, 1), (glow, 1)):  # two passes: wide soft halo + tight glow
            img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(r)))
        img = Image.alpha_composite(img, layer)
    if label and has:
        names = st.get("label_fonts", ["segoeuib.ttf", "arialbd.ttf"])
        size = 42
        while size > 18 and _font(names, size).getlength(label) > maxw:
            size -= 2
        ImageDraw.Draw(img).text((K / 2, K * (0.735 if glyph else 0.5) + dy), label, fill=tuple(st.get("label_color", (240, 240, 240))),
                                 font=_font(names, size), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
    if st.get("frame") == "brackets":
        # inset 28 px of 216 (~9 px on the 72 px key): the pad's bezel hides roughly the outer 4-5 px
        d, n, w, m = ImageDraw.Draw(img), 36, 7, 28
        c = color + (230 if has else 90,)  # blank keys get the same brackets, just fainter
        if not has:
            layer = Image.new("RGBA", (K, K), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
        off = int(round(dy))
        for (x, y, sx, sy) in ((m, m, 1, 1), (K - m, m, -1, 1), (m, K - m, 1, -1), (K - m, K - m, -1, -1)):
            y += off
            d.line((x, y, x + sx * n, y), fill=c, width=w)
            d.line((x, y, x, y + sy * n), fill=c, width=w)
        if not has:
            img = Image.alpha_composite(img, layer)  # semi-transparent brackets need compositing
    return img.convert("RGB")


def render_slot(cfg, slot, spec=None):
    """Image for one slot under the config's active pack, or None for a blank key on the modern pack."""
    spec = cfg["keys"].get(str(slot), {}) if spec is None else spec
    pack = load_pack(cfg.get("icon_pack", "modern"))
    shift = row_shift(cfg, slot)
    if pack is None:
        return hardware.render(spec, shift * 170 / 72) if _has_content(spec) else None
    return render_key(pack, slot, spec, shift * K / 72)


def render_grid(cfg, pack_id, tile_px=108, gap=12):
    """Preview of all 15 keys under a given pack using the config's current key contents."""
    pack = load_pack(pack_id)
    w, h = 5 * tile_px + 4 * gap, 3 * tile_px + 2 * gap
    out = Image.new("RGB", (w, h), (8, 8, 10))
    for slot in range(1, 16):
        spec = cfg["keys"].get(str(slot), {})
        shift = row_shift(cfg, slot)
        img = (hardware.render(spec, shift * 170 / 72) if pack is None and _has_content(spec)
               else (render_key(pack, slot, spec, shift * K / 72) if pack else None))
        if img is None:
            continue
        row, col = divmod(slot - 1, 5)
        out.paste(img.resize((tile_px, tile_px), Image.LANCZOS), (col * (tile_px + gap), row * (tile_px + gap)))
    return out
