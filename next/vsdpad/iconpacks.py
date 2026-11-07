"""Icon packs: sets of one-colour icons that are drawn in the theme's colours.

A pack is a folder with pack.json and either atlas.png (a grid of greyscale masks, white = ink; the
built-in packs) or icons/<name>.png (packs you import). Built-in packs live in vsdpad/iconpacks/,
imported ones in data/iconpacks/.

A key uses an icon with "icon": "<pack>:<name>" (e.g. "outline:house"), or "theme:<name>" to follow
the theme's own pack (the "icon_pack" field of its pack.json; "outline" if it has none). If the
theme's pack has no icon by that name, the outline one is used.

pack.json: {"id", "name", "style": "outline|filled|bold|duotone|neon|pixel|...", "author", "license",
            "source", "tint": true, "icons": [names], "tags": {name: "search words"},
            "cell": 96, "cols": 32 (atlas only)}
"tint": false keeps an imported pack's own colours instead of tinting it.
"""
import functools, io, json, math, os, re, shutil, tempfile, threading, zipfile

from PIL import Image, ImageFilter

from . import PKG_DIR

BUILTIN_DIR = os.path.join(PKG_DIR, "iconpacks")
_user_dir = [None]
ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
REF_RE = re.compile(r"^([a-z0-9_-]{1,32}):([a-z0-9][a-z0-9_.-]{0,63})$")
FALLBACK = "outline"
RESERVED = {"theme", "img", "steam"}
CELL = 96            # size imported icons are stored at
MAX_ZIP = 30_000_000
MAX_FILE = 2_000_000
MAX_ICONS = 2000
_lock = threading.Lock()


def set_user_dir(path):
    _user_dir[0] = path
    if path:
        os.makedirs(path, exist_ok=True)
    _load.cache_clear()


def user_dir():
    return _user_dir[0]


def parse(ref):
    """(pack, name) for an icon reference like "outline:house", else None (a plain text glyph)."""
    m = REF_RE.match(ref) if isinstance(ref, str) else None
    return (m.group(1), m.group(2)) if m else None


def _pack_dir(pid):
    if not ID_RE.match(pid or "") or pid in RESERVED:
        return None, False
    ud = user_dir()
    if ud and os.path.isfile(os.path.join(ud, pid, "pack.json")):
        return os.path.join(ud, pid), False
    d = os.path.join(BUILTIN_DIR, pid)
    return (d, True) if os.path.isfile(os.path.join(d, "pack.json")) else (None, False)


def _mtime(d):
    try:
        return max(os.path.getmtime(os.path.join(d, "pack.json")), os.path.getmtime(d))
    except OSError:
        return 0


def load(pid):
    """The pack dict (with "dir", "builtin", "index" {name: position}), or None."""
    d, builtin = _pack_dir(pid)
    return _load(d, builtin, _mtime(d)) if d else None


@functools.lru_cache(maxsize=32)
def _load(d, builtin, mtime):
    try:
        with open(os.path.join(d, "pack.json"), encoding="utf-8") as f:
            p = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(p, dict) or not isinstance(p.get("icons"), list):
        return None
    p["dir"], p["builtin"] = d, builtin
    p["index"] = {n: i for i, n in enumerate(p["icons"])}
    p["version"] = mtime
    return p


def list_packs():
    out, seen = [], set()
    for base, builtin in ((user_dir(), False), (BUILTIN_DIR, True)):
        if not base or not os.path.isdir(base):
            continue
        for pid in sorted(os.listdir(base)):
            if pid in seen or not ID_RE.match(pid):
                continue
            p = load(pid)
            if p and p["builtin"] == builtin:
                seen.add(pid)
                out.append({"id": pid, "name": p.get("name", pid), "style": p.get("style", ""), "count": len(p["icons"]),
                            "author": p.get("author", ""), "license": p.get("license", ""), "source": p.get("source", ""),
                            "tint": p.get("tint", True), "builtin": builtin, "version": p["version"]})
    order = ["outline", "filled", "bold", "duotone", "neon", "pixel"]
    out.sort(key=lambda p: (not p["builtin"], order.index(p["id"]) if p["id"] in order else 99, p["name"].lower()))
    return out


def index(pid):
    """What the icon picker needs: names, search words and where each icon sits in the sprite."""
    p = load(pid)
    if not p:
        raise ValueError("unknown icon pack")
    return {"id": pid, "name": p.get("name", pid), "style": p.get("style", ""), "icons": p["icons"],
            "tags": p.get("tags", {}), "cell": _cell(p), "cols": _cols(p), "tint": p.get("tint", True),
            "version": p["version"], "pending": p.get("pending", [])}


def theme_pack(theme):
    pid = (theme or {}).get("icon_pack") if isinstance(theme, dict) else None
    return pid if isinstance(pid, str) and load(pid) else FALLBACK


def resolve(ref, theme=None):
    """Turn "theme:<name>" into "<theme's pack>:<name>" (or the outline one). Other values are returned as they are."""
    pr = parse(ref)
    if not pr or pr[0] != "theme":
        return ref
    for pid in (theme_pack(theme), FALLBACK, "pixel"):  # outline/filled/bold/duotone/neon share names; pixel has its own
        p = load(pid)
        if p and pr[1] in p["index"]:
            return f"{pid}:{pr[1]}"
    return f"{FALLBACK}:{pr[1]}"


def version(ref):
    """Changes whenever the pack behind ref changes (for render caches)."""
    pr = parse(ref)
    p = load(pr[0]) if pr else None
    return p["version"] if p else 0


# ---- pixels ---------------------------------------------------------------------------------------
def _cell(p):
    return int(p.get("cell", CELL))


def _cols(p):
    return int(p.get("cols", 32))


@functools.lru_cache(maxsize=16)
def _atlas(path, mtime):
    with Image.open(path) as im:
        return im.convert("L") if im.mode in ("L", "LA", "P", "1") else im.convert("RGBA")


def _source(p, name):
    """The stored icon: an "L" mask, or RGBA for packs with tint false. None if missing."""
    i = p["index"].get(name)
    if i is None:
        return None
    a = os.path.join(p["dir"], "atlas.png")
    if os.path.isfile(a):
        img = _atlas(a, os.path.getmtime(a))
        c, cols = _cell(p), _cols(p)
        r, k = divmod(i, cols)
        return img.crop((k * c, r * c, k * c + c, r * c + c))
    f = os.path.join(p["dir"], "icons", name + ".png")
    try:
        with Image.open(f) as im:
            return im.convert("RGBA") if im.mode in ("RGBA", "RGB", "P") and not p.get("tint", True) else _as_mask(im)
    except OSError:
        return None


def _as_mask(im):
    """Ink = the opaque parts; for a picture with no see-through parts, ink = whatever differs from the corner colour."""
    rgba = im.convert("RGBA")
    a = rgba.getchannel("A")
    if a.getextrema()[0] < 250:
        return a
    g = rgba.convert("L")
    if g.getpixel((0, 0)) > 127:  # dark icon on a light background
        g = g.point(lambda v: 255 - v)
    return g.point(lambda v: min(255, v * 2) if v > 24 else 0)


@functools.lru_cache(maxsize=512)
def _scaled(pid, name, size, ver):
    p = load(pid)
    if not p:
        return None
    src = _source(p, name)
    if src is None:
        return None
    if p.get("style") == "pixel":
        n = max(1, size // src.width)
        return src.resize((src.width * n, src.height * n), Image.NEAREST)
    return src.resize((size, size), Image.LANCZOS)


def image(ref, size, color, theme=None):
    """The icon as an RGBA picture size x size in color, or None if ref isn't a known icon."""
    pr = parse(resolve(ref, theme))
    if not pr:
        return None
    p = load(pr[0])
    if not p:
        return None
    src = _scaled(pr[0], pr[1], int(size), p["version"])
    if src is None:
        return None
    if src.mode == "RGBA":  # own colours
        return src.copy()
    out = Image.new("RGBA", src.size, tuple(color[:3]) + (255,))
    out.putalpha(src)
    if p.get("style") == "neon":  # a hot, near-white core inside a coloured tube
        core = src.filter(ImageFilter.MinFilter(3)).point(lambda v: v * 0.8)
        white = Image.new("RGBA", src.size, tuple(min(255, c + 150) for c in color[:3]) + (255,))
        white.putalpha(core)
        out = Image.alpha_composite(out, white)
    return out


def known(ref, theme=None):
    pr = parse(resolve(ref, theme))
    p = load(pr[0]) if pr else None
    return bool(p and pr[1] in p["index"])


@functools.lru_cache(maxsize=16)
def _sprite(pid, ver):
    p = load(pid)
    c, cols, n = _cell(p), _cols(p), len(p["icons"])
    a = os.path.join(p["dir"], "atlas.png")
    if os.path.isfile(a):
        atlas = _atlas(a, os.path.getmtime(a))
    else:
        rows = max(1, math.ceil(n / cols))
        atlas = Image.new("RGBA" if not p.get("tint", True) else "L", (cols * c, rows * c), 0)
        for i, name in enumerate(p["icons"]):
            src = _source(p, name)
            if src is not None:
                r, k = divmod(i, cols)
                src = src.resize((c, c), Image.NEAREST if p.get("style") == "pixel" else Image.LANCZOS)
                atlas.paste(src, (k * c, r * c))
    if atlas.mode == "L":
        rgba = Image.new("RGBA", atlas.size, (255, 255, 255, 255))
        rgba.putalpha(atlas)
        atlas = rgba
    buf = io.BytesIO()
    atlas.save(buf, "PNG")
    return buf.getvalue()


def sprite(pid):
    """One PNG with every icon of a pack (white, or own colours), laid out as index() says."""
    p = load(pid)
    if not p:
        raise ValueError("unknown icon pack")
    return _sprite(pid, p["version"])


# ---- your own packs -------------------------------------------------------------------------------
def _slug(s, maxlen=64):
    s = re.sub(r"[^a-z0-9_.-]+", "-", s.lower()).strip("-._")
    return s[:maxlen].strip("-._")


def import_zip(data, pack_id=None, name=None, tint=True, replace=False):
    """Make a pack from a .zip of PNG and SVG files. Returns {"id", "count", "pending": [svg names]}.

    PNGs are stored as they are (scaled to 96 px). SVGs can't be drawn here, so they're kept in svg/ and
    listed as pending: the settings page draws each one and sends it back with add_drawn().
    An optional pack.json in the zip may give id, name, author, license, style, tint and tags.
    """
    ud = user_dir()
    if not ud:
        raise ValueError("icon packs aren't set up")
    if len(data) > MAX_ZIP:
        raise ValueError("That icon pack is too big (30 MB max).")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That isn't a .zip file.") from None
    meta, pngs, svgs = {}, {}, {}
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ICONS + 10:
        raise ValueError(f"Too many files (up to {MAX_ICONS} icons).")
    for info in infos:
        base = info.filename.replace("\\", "/").split("/")[-1]
        if base.startswith(".") or "__MACOSX" in info.filename:
            continue
        if info.file_size > MAX_FILE:
            raise ValueError(f"{base} is too big.")
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        if base.lower() == "pack.json":
            try:
                meta = json.loads(zf.read(info).decode("utf-8"))
            except ValueError:
                raise ValueError("pack.json is not valid JSON.") from None
            if not isinstance(meta, dict):
                raise ValueError("pack.json must be an object.")
            continue
        n = _slug(stem)
        if not n or ext not in (".png", ".svg"):
            continue
        while n in pngs or n in svgs:
            n = _slug(n + "-2")
        if ext == ".png":
            try:
                with Image.open(io.BytesIO(zf.read(info))) as im:
                    if im.width * im.height > 16_000_000:
                        raise ValueError(f"{base} is too large.")
                    im = im.convert("RGBA")
            except (OSError, SyntaxError):
                raise ValueError(f"{base} is not a valid PNG.") from None
            im.thumbnail((CELL, CELL), Image.LANCZOS)
            sq = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
            sq.paste(im, ((CELL - im.width) // 2, (CELL - im.height) // 2))
            pngs[n] = sq
        else:
            blob = zf.read(info)
            if b"<svg" not in blob[:4096].lower():
                raise ValueError(f"{base} is not an SVG.")
            svgs[n] = blob
    if not pngs and not svgs:
        raise ValueError("The zip has no PNG or SVG icons in it.")
    if len(pngs) + len(svgs) > MAX_ICONS:
        raise ValueError(f"Too many icons (up to {MAX_ICONS}).")
    zip_id = meta.get("id") if isinstance(meta.get("id"), str) else None
    pid = _slug(pack_id or zip_id or name or meta.get("name") or "my-icons", 32).replace(".", "-")
    if not ID_RE.match(pid) or pid in RESERVED:
        raise ValueError("Pick a name for the pack made of letters and numbers.")
    if os.path.isdir(os.path.join(BUILTIN_DIR, pid)):
        raise ValueError(f"{pid!r} is a built-in pack; pick another name.")
    dest = os.path.join(ud, pid)
    if os.path.exists(dest) and not replace:
        raise ValueError(f"You already have an icon pack called {pid!r}.")
    tint = bool(meta.get("tint", tint))
    pack = {"id": pid, "name": str(name or meta.get("name") or pid)[:40], "style": str(meta.get("style", "custom"))[:20],
            "author": str(meta.get("author", ""))[:80], "license": str(meta.get("license", ""))[:80],
            "source": str(meta.get("source", ""))[:200], "tint": tint, "cell": CELL,
            "icons": sorted(pngs), "pending": sorted(svgs)}
    tags = meta.get("tags")
    if isinstance(tags, dict):
        pack["tags"] = {_slug(k): str(v)[:200] for k, v in tags.items() if isinstance(k, str) and _slug(k)}
    tmp = tempfile.mkdtemp(dir=ud, prefix=".import-")
    try:
        os.makedirs(os.path.join(tmp, "icons"))
        for n, im in pngs.items():
            im.save(os.path.join(tmp, "icons", n + ".png"))
        if svgs:
            os.makedirs(os.path.join(tmp, "svg"))
            for n, blob in svgs.items():
                with open(os.path.join(tmp, "svg", n + ".svg"), "wb") as f:
                    f.write(blob)
        with open(os.path.join(tmp, "pack.json"), "w", encoding="utf-8") as f:
            json.dump(pack, f, indent=1)
        with _lock:
            if os.path.exists(dest):
                shutil.rmtree(dest)
            os.replace(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _load.cache_clear()
    return {"id": pid, "count": len(pngs), "pending": pack["pending"]}


def _user_pack(pid):
    d, builtin = _pack_dir(pid)
    if not d or builtin:
        raise ValueError("unknown icon pack (only your own packs can be changed)")
    return d


def pending_svg(pid, name):
    """The raw SVG of an icon waiting to be drawn by the settings page."""
    d = _user_pack(pid)
    if not NAME_RE.match(name or ""):
        raise ValueError("bad icon name")
    try:
        with open(os.path.join(d, "svg", name + ".svg"), "rb") as f:
            return f.read()
    except OSError:
        raise ValueError("no such SVG waiting") from None


def add_drawn(pid, name, png):
    """Store an SVG icon that the settings page has drawn as a PNG, and mark it done."""
    d = _user_pack(pid)
    if not NAME_RE.match(name or ""):
        raise ValueError("bad icon name")
    with _lock:
        with open(os.path.join(d, "pack.json"), encoding="utf-8") as f:
            pack = json.load(f)
        if name not in pack.get("pending", []):
            raise ValueError("that icon isn't waiting to be drawn")
        try:
            with Image.open(io.BytesIO(png)) as im:
                if im.width * im.height > 4_000_000:
                    raise ValueError("too large")
                im = im.convert("RGBA")
        except (OSError, SyntaxError):
            raise ValueError("That drawing didn't arrive as a PNG.") from None
        im.thumbnail((CELL, CELL), Image.LANCZOS)
        sq = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
        sq.paste(im, ((CELL - im.width) // 2, (CELL - im.height) // 2))
        sq.save(os.path.join(d, "icons", name + ".png"))
        pack["pending"].remove(name)
        pack["icons"] = sorted(set(pack["icons"]) | {name})
        tmp = os.path.join(d, "pack.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pack, f, indent=1)
        os.replace(tmp, os.path.join(d, "pack.json"))
        try:
            os.remove(os.path.join(d, "svg", name + ".svg"))
        except OSError:
            pass
    _load.cache_clear()
    return {"left": len(pack["pending"])}


def delete(pid):
    d = _user_pack(pid)
    with _lock:
        shutil.rmtree(d)
    _load.cache_clear()


def export_zip(pid):
    p = load(pid)
    if not p:
        raise ValueError("unknown icon pack")
    buf = io.BytesIO()
    meta = {k: p[k] for k in ("id", "name", "style", "author", "license", "source", "tint", "tags") if k in p}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("pack.json", json.dumps(meta, indent=1))
        for name in p["icons"]:
            src = _source(p, name)
            if src is None:
                continue
            if src.mode == "L":
                im = Image.new("RGBA", src.size, (255, 255, 255, 255))
                im.putalpha(src)
                src = im
            b = io.BytesIO()
            src.save(b, "PNG")
            zf.writestr(f"icons/{name}.png", b.getvalue())
    return buf.getvalue()
