"""Make one-colour (tintable) versions of your app icons in each icon style.

Usage: python tools/app_icon_styles.py <out folder> <folder holding clones of the app repos>
then:  python tools/build_iconpacks.py <npm packages folder> vsdpad/iconpacks 96 <out folder>
"""
import io, os, sys
import cairosvg
from PIL import Image, ImageChops, ImageFilter, ImageOps

R = (sys.argv[2].rstrip("/\\") + "/") if len(sys.argv) > 2 else "./"
PICK = {"raccboard": "raccboard/branding/raccboard-icon.svg", "bingebandit": "BingeBandit/web/public/brand/app-icon-512.png",
        "wishes": "wishes/public/icon.svg", "dendrop": "dendrop/packages/desktop/build/icon.svg", "lootden": "lootden/brand/icon.png",
        "den-duty": "Den-Duty/brand/logo/den-duty-logo-square.svg", "weather-scout": "weather-scout/web/public/icon-512.png",
        "statscraps": "sports-tracker/public/img/statscraps-icon-512.png", "wavelength": "wavelength/src-tauri/icons/icon.png",
        "nighttime-ninja": "Nighttime-Ninja/web/public/icon.svg", "cinemask": "Cinemask/docs/icon.png",
        "watchmask": "WatchMask/src/static/img/floppy-icon.png", "matinee": "matinee/brand/icon.svg"}
N = 384


def load(p):
    p = R + p
    im = Image.open(io.BytesIO(cairosvg.svg2png(url=p, output_width=N, output_height=N))) if p.endswith(".svg") else Image.open(p)
    im = im.convert("RGBA")
    if "BingeBandit" in p:  # the raccoon sits on an inner tile inside a frame
        w = im.width
        im = im.crop((int(w * .19), int(w * .19), int(w * .81), int(w * .81)))
    return im.resize((N, N), Image.LANCZOS)


def strip_tile(im):
    """If the icon sits on a solid/gradient tile, make the tile see-through (keyed on colours along the tile's inner edge)."""
    a = im.getchannel("A")
    rgb = im.convert("RGB")
    m = int(N * 0.07)
    samples = [rgb.getpixel((x, y)) for x, y in ((m, N // 2), (N - m, N // 2), (N // 2, m), (m, m * 2), (N - m, m * 2), (m, N - 2 * m), (N - m, N - 2 * m))]
    if a.getpixel((m, N // 2)) < 200:  # transparent around: no tile
        return im
    keep = Image.new("L", (N, N), 0)
    px, kp = rgb.load(), keep.load()
    for y in range(N):
        for x in range(N):
            c = px[x, y]
            if min(sum((c[i] - s[i]) ** 2 for i in range(3)) for s in samples) > 55 ** 2:
                kp[x, y] = 255
    keep = keep.filter(ImageFilter.MedianFilter(5))
    out = im.copy()
    out.putalpha(ImageChops.multiply(a, keep))
    return out


def fit(mask, size, pad):
    bb = mask.point(lambda v: 255 if v > 40 else 0).getbbox()
    if bb:
        mask = mask.crop(bb)
    s = (size - 2 * pad) / max(mask.size)
    mask = mask.resize((max(1, round(mask.width * s)), max(1, round(mask.height * s))), Image.LANCZOS)
    out = Image.new("L", (size, size), 0)
    out.paste(mask, ((size - mask.width) // 2, (size - mask.height) // 2))
    return out


def styles(im):
    sub = strip_tile(im)
    a = sub.getchannel("A").point(lambda v: 255 if v > 110 else 0).filter(ImageFilter.MedianFilter(5))
    lum = sub.convert("L")
    dark = ImageChops.multiply(a, lum.point(lambda v: 255 if v < 75 else 0)).filter(ImageFilter.MedianFilter(5))
    share = sum(dark.histogram()[128:]) / max(1, sum(a.histogram()[128:]))
    if share > 0.75:  # a mostly dark logo (e.g. a silhouette): its shape is the icon, no features to carve
        dark = Image.new("L", a.size, 0)
    filled = ImageChops.subtract(a, dark.filter(ImageFilter.MaxFilter(5)))
    # outlines: edges of the silhouette and of the dark features
    poster = ImageChops.add(a.point(lambda v: v // 2), ImageChops.subtract(a, dark).point(lambda v: v // 2))
    edges = poster.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 40 else 0)
    thin = edges.filter(ImageFilter.MaxFilter(7))
    bold = edges.filter(ImageFilter.MaxFilter(13))
    light = edges.filter(ImageFilter.MaxFilter(5))
    duo = ImageChops.lighter(thin, a.point(lambda v: v * 0.3))
    blur = lambda m: m.filter(ImageFilter.GaussianBlur(1.2))
    out = {"outline": fit(blur(thin), 96, 8), "filled": fit(blur(filled), 96, 8), "bold": fit(blur(bold), 96, 8),
           "duotone": fit(blur(duo), 96, 8), "neon": fit(blur(light), 96, 8)}
    px = fit(filled, 96, 4).resize((24, 24), Image.BOX).point(lambda v: 255 if v > 110 else 0)
    out["pixel"] = px
    return out


if __name__ == "__main__":
    dst = sys.argv[1]
    for name, p in PICK.items():
        for style, m in styles(load(p)).items():
            os.makedirs(os.path.join(dst, style), exist_ok=True)
            m.save(os.path.join(dst, style, f"app-{name}.png"))
        print(name)
