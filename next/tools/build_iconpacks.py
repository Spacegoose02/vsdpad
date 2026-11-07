"""Build vsdpad icon packs (atlas.png + pack.json) from Phosphor and Pixelarticons SVGs."""
import io, json, math, os, re, sys
import cairosvg
from PIL import Image

SRC, OUT = sys.argv[1], sys.argv[2]
COLS = 32

def meta_phosphor():
    txt = open(os.path.join(SRC, "phosphor-icons-core-2.1.1/package/dist/index.mjs"), encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'name: "([a-z0-9-]+)",\s*pascal_name:.*?categories: \[([^\]]*)\].*?tags: \[([^\]]*)\]', txt, re.S):
        name = m.group(1)
        cats = re.findall(r'e\.([A-Z]+)', m.group(2))
        tags = [t for t in re.findall(r'"([^"]*)"', m.group(3)) if not t.startswith("*")]
        out[name] = sorted(set(t.lower() for t in tags + [c.lower() for c in cats]))
    return out

def raster(path, px):
    png = cairosvg.svg2png(url=path, output_width=px, output_height=px)
    return Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A")

EXTRA = sys.argv[4] if len(sys.argv) > 4 else None  # folder of <style>/app-*.png masks (tools/app_icon_styles.py)


def build(pid, name, style, files, cell, meta, author, license_, source, lic_file):
    names = sorted(files)
    extra = {}
    if EXTRA and os.path.isdir(os.path.join(EXTRA, style)):
        for f in sorted(os.listdir(os.path.join(EXTRA, style))):
            n = f[:-4]
            extra[n] = os.path.join(EXTRA, style, f)
            meta = dict(meta, **{n: ["my apps", "app"] + n[4:].split("-")})
        names = sorted(extra) + names
    rows = math.ceil(len(names) / COLS)
    atlas = Image.new("L", (COLS * cell, rows * cell), 0)
    for i, n in enumerate(names):
        r, c = divmod(i, COLS)
        if n in extra:
            with Image.open(extra[n]) as im:
                atlas.paste(im.convert("L").resize((cell, cell), Image.NEAREST if cell <= 24 else Image.LANCZOS), (c * cell, r * cell))
        else:
            atlas.paste(raster(files[n], cell), (c * cell, r * cell))
    d = os.path.join(OUT, pid)
    os.makedirs(d, exist_ok=True)
    atlas.save(os.path.join(d, "atlas.png"), optimize=True)
    pack = {"id": pid, "name": name, "style": style, "author": author, "license": license_, "source": source,
            "cell": cell, "cols": COLS, "icons": names, "tags": {n: " ".join(meta.get(n, [])) for n in names if meta.get(n)}}
    with open(os.path.join(d, "pack.json"), "w", encoding="utf-8") as f:
        json.dump(pack, f, separators=(",", ":"), ensure_ascii=False)
    with open(lic_file, encoding="utf-8") as f, open(os.path.join(d, "LICENSE.txt"), "w", encoding="utf-8") as g:
        g.write(f.read())
    print(pid, len(names), os.path.getsize(os.path.join(d, "atlas.png")))

pm = meta_phosphor()
ph = os.path.join(SRC, "phosphor-icons-core-2.1.1/package")
for pid, nm, style, sub, suffix in (("outline", "Outline", "outline", "regular", ""), ("filled", "Filled", "filled", "fill", "-fill"),
                                    ("bold", "Bold", "bold", "bold", "-bold"), ("duotone", "Duotone", "duotone", "duotone", "-duotone"),
                                    ("neon", "Neon", "neon", "light", "-light")):
    d = os.path.join(ph, "assets", sub)
    files = {f[:-4][:len(f) - 4 - len(suffix)] if suffix else f[:-4]: os.path.join(d, f) for f in os.listdir(d) if f.endswith(".svg")}
    build(pid, nm, style, files, int(sys.argv[3]) if len(sys.argv) > 3 else 96, pm, "Phosphor Icons", "MIT",
          "https://phosphoricons.com", os.path.join(ph, "LICENSE"))
px = os.path.join(SRC, "pixelarticons-2.4.2/package")
d = os.path.join(px, "svg")
files = {f[:-4]: os.path.join(d, f) for f in os.listdir(d) if f.endswith(".svg") and not f.endswith("-sharp.svg")}
pxmeta = {n: n.split("-") for n in files}
build("pixel", "Pixel", "pixel", files, 24, pxmeta, "Gerrit Halfmann (pixelarticons)", "MIT",
      "https://pixelarticons.com", os.path.join(px, "LICENSE"))
