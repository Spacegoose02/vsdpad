"""Import a theme from a .zip safely: only pack.json and pictures, no paths, size limits."""
import io, json, os, re, shutil, tempfile, zipfile

from PIL import Image

from .render import ID_RE, THEMES_DIR

MAX_ZIP = 20_000_000
MAX_FILE = 10_000_000
MAX_FILES = 40
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}\.(png|jpg|jpeg)$")


def import_zip(data, themes_dir=THEMES_DIR, replace=False):
    if len(data) > MAX_ZIP:
        raise ValueError("That theme file is too big (20 MB max).")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That isn't a .zip file.") from None
    files = {}
    for info in zf.infolist()[:MAX_FILES + 1]:
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/").split("/")[-1]  # flatten: ignore any folders inside the zip
        if name != "pack.json" and not NAME_RE.match(name):
            continue
        if info.file_size > MAX_FILE:
            raise ValueError(f"{name} is too big.")
        files[name] = zf.read(info)
    if len(files) > MAX_FILES:
        raise ValueError("Too many files in that theme.")
    if "pack.json" not in files:
        raise ValueError("The zip has no pack.json.")
    try:
        pack = json.loads(files["pack.json"].decode("utf-8"))
    except ValueError:
        raise ValueError("pack.json is not valid JSON.") from None
    if not isinstance(pack, dict) or not isinstance(pack.get("id"), str) or not ID_RE.match(pack["id"]) or pack["id"] == "modern":
        raise ValueError("pack.json needs an id like 'my-theme' (lowercase letters, numbers, - and _).")
    for k in ("name", "description", "author"):
        if k in pack and not isinstance(pack[k], str):
            raise ValueError(f"pack.json: {k} must be text")
    if not isinstance(pack.get("style", {}), dict):
        raise ValueError("pack.json: style must be an object")
    wp = pack.get("wallpaper")
    if wp is not None and (not isinstance(wp, str) or os.path.basename(wp) not in files):
        raise ValueError("pack.json names a wallpaper that isn't in the zip.")
    for name, blob in files.items():  # every picture must really be a picture; re-encode to drop anything else
        if name == "pack.json":
            continue
        try:
            with Image.open(io.BytesIO(blob)) as im:
                im.verify()
            with Image.open(io.BytesIO(blob)) as im:
                if im.width * im.height > 40_000_000:
                    raise ValueError(f"{name} is too large.")
                out = io.BytesIO()
                im.convert("RGB").save(out, "PNG" if name.lower().endswith(".png") else "JPEG", quality=92)
                files[name] = out.getvalue()
        except (OSError, SyntaxError):
            raise ValueError(f"{name} is not a valid picture.") from None
    dest = os.path.join(themes_dir, pack["id"])
    if os.path.exists(dest) and not replace:
        raise ValueError(f"A theme called {pack['id']!r} already exists.")
    tmp = tempfile.mkdtemp(dir=themes_dir, prefix=".import-")
    try:
        for name, blob in files.items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(json.dumps(pack, indent=2).encode() if name == "pack.json" else blob)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.replace(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return pack["id"]


def export_zip(theme_id, themes_dir=THEMES_DIR):
    d = os.path.join(themes_dir, theme_id)
    if not ID_RE.match(theme_id) or not os.path.isfile(os.path.join(d, "pack.json")):
        raise ValueError("unknown theme")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(d)):
            if name == "pack.json" or NAME_RE.match(name):
                zf.write(os.path.join(d, name), name)
    return buf.getvalue()
