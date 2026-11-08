"""Sharing packs: one .vsdpack file with a profile's pages plus the pictures, themes and icon packs they use.

A .vsdpack is a zip:
  manifest.json            {"vsdpad_pack": 1, "name", "created", "app", "contents": {...}}
  profile.json             {"name", "layouts", "profiles" (app rules), "theme"}     (optional)
  images/<file>            pictures used on keys and page backgrounds
  themes/<id>.zip          themes (the same zip as Themes > Download)
  iconpacks/<id>.zip       your own icon packs (the same zip as the icon picker's download)

Never in a pack: plugin settings (tokens, keys, addresses), your pads, paired phones, history.
Importing adds the pages as a new profile (nothing of yours is replaced) and installs the themes and icon packs
you don't have yet.
"""
import io, json, os, re, time, zipfile

from PIL import Image

from . import __version__, iconpacks, layoutprofiles, themes_io
from .migrate import migrate
from .render import ID_RE as THEME_ID_RE, list_themes

PACK_VERSION = 1
MAX_PACK = 80_000_000
MAX_IMAGE = 8_000_000
MAX_FILES = 600
IMAGE_NAME_RE = re.compile(r"^[0-9a-f]{16,64}\.(png|jpg|jpeg|gif|webp)$")
FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "gif": "GIF", "webp": "WEBP"}


class PackError(ValueError):
    pass


# ---- what a profile uses --------------------------------------------------------------------------------------
def used(data):
    """{"images", "themes", "iconpacks"} referenced by {layouts, theme} (sorted lists of ids / file names)."""
    images, themes, packs = set(), set(), set()
    if data.get("theme"):
        themes.add(data["theme"])
    for lay in data.get("layouts") or []:
        keys = list(lay.get("buttons", {}).values())
        for pg in lay.get("pages", []):
            keys += list(pg.get("keys", {}).values())
            if pg.get("theme"):
                themes.add(pg["theme"])
            if str(pg.get("backdrop") or "").startswith("img:"):
                images.add(pg["backdrop"][4:])
        for k in keys:
            if not isinstance(k, dict):
                continue
            if str(k.get("image") or "").startswith("img:"):
                images.add(k["image"][4:])
            ref = iconpacks.parse(k.get("icon"))
            if ref:
                packs.add(ref[0])
    builtin_packs = {p["id"] for p in iconpacks.list_packs() if p["builtin"]}
    return {"images": sorted(i for i in images if IMAGE_NAME_RE.match(i)),
            "themes": sorted(t for t in themes if t != "modern" and THEME_ID_RE.match(t)),
            "iconpacks": sorted(p for p in packs if p not in builtin_packs and p not in iconpacks.RESERVED)}


def choices(app):
    """For the settings page: what can go in a pack, and what each profile uses."""
    cfg = app.store.get()
    profiles = []
    for p in layoutprofiles.summary(cfg):
        data = layoutprofiles.saved_data(cfg, p["id"])
        u = used(data)
        profiles.append({**p, "pages": sum(len(l["pages"]) for l in data["layouts"]), **u})
    return {"profiles": profiles,
            "themes": [{"id": t["id"], "name": t["name"]} for t in list_themes() if t["id"] != "modern"],
            "iconpacks": [{"id": p["id"], "name": p["name"], "count": p["count"]} for p in iconpacks.list_packs() if not p["builtin"]]}


# ---- export ---------------------------------------------------------------------------------------------------
def export(app, profile=None, themes=(), packs=(), name=""):
    """Bytes of a .vsdpack. profile: a profile id/name, "" for none (themes / icons only), None for the one in use."""
    cfg = app.store.get()
    contents = {"profile": None, "images": 0, "themes": [], "iconpacks": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        want_themes, want_packs = set(themes or ()), set(packs or ())
        if profile != "":
            info = layoutprofiles.find(cfg, profile) if profile else layoutprofiles.active(cfg)
            data = layoutprofiles.saved_data(cfg, info["id"])
            u = used(data)
            want_themes |= set(u["themes"])
            want_packs |= set(u["iconpacks"])
            zf.writestr("profile.json", json.dumps({"name": info["name"], **data}, indent=1, ensure_ascii=False))
            contents["profile"] = {"name": info["name"], "pages": sum(len(l["pages"]) for l in data["layouts"])}
            for img in u["images"]:
                path = app.image_path("img:" + img)
                if path:
                    zf.write(path, "images/" + img)
                    contents["images"] += 1
            name = name or info["name"]
        for tid in sorted(want_themes):
            try:
                zf.writestr(f"themes/{tid}.zip", themes_io.export_zip(tid))
                contents["themes"].append(tid)
            except ValueError:
                pass   # a theme that's gone: the keys fall back to the normal look
        for pid in sorted(want_packs):
            try:
                zf.writestr(f"iconpacks/{pid}.zip", iconpacks.export_zip(pid))
                contents["iconpacks"].append(pid)
            except ValueError:
                pass
        if not (contents["profile"] or contents["themes"] or contents["iconpacks"]):
            raise PackError("Pick something to put in the pack.")
        manifest = {"vsdpad_pack": PACK_VERSION, "name": (name or "My pack")[:60], "created": time.strftime("%Y-%m-%d %H:%M"),
                    "app": __version__, "contents": contents}
        zf.writestr("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
    return buf.getvalue()


def file_name(name):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name or "").strip("-")[:40] or "vsdpad"
    return f"{slug}.vsdpack"


# ---- reading a pack -------------------------------------------------------------------------------------------
def _open(blob):
    """(manifest, profile data or None, {image name: bytes}, {theme id: zip}, {icon pack id: zip}).
    Also takes a layout file from Settings > Backup (.json): its pages become the profile."""
    if len(blob) > MAX_PACK:
        raise PackError("That file is too big (80 MB max).")
    if blob[:1] in (b"{", b"\xef"):
        try:
            data = json.loads(blob.decode("utf-8-sig"))
        except ValueError:
            raise PackError("That file isn't a VSD Pad pack or layout.") from None
        cfg = data.get("config", data) if isinstance(data, dict) and "vsdpad_export" in data else data
        if not isinstance(cfg, dict) or not (cfg.get("layouts") or cfg.get("pages") or cfg.get("keys")):
            raise PackError("That file isn't a VSD Pad pack or layout.")
        cfg, _, _ = migrate(cfg)
        prof = {k: cfg[k] for k in layoutprofiles.SAVED_KEYS if k in cfg}
        return {"name": "Imported layout"}, {"name": "Imported layout", **prof}, {}, {}, {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise PackError("That file isn't a VSD Pad pack (.vsdpack).") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise PackError("That pack has too many files.")
    if sum(i.file_size for i in infos) > MAX_PACK * 2:
        raise PackError("That pack is too big.")
    names = {i.filename: i for i in infos}
    if "manifest.json" not in names:
        raise PackError("That file isn't a VSD Pad pack (no manifest.json).")

    def read_json(n):
        try:
            v = json.loads(zf.read(names[n]).decode("utf-8"))
        except ValueError:
            raise PackError(f"{n} in the pack is damaged.") from None
        if not isinstance(v, dict):
            raise PackError(f"{n} in the pack is damaged.")
        return v
    manifest = read_json("manifest.json")
    if not isinstance(manifest.get("vsdpad_pack"), int) or manifest["vsdpad_pack"] > PACK_VERSION:
        raise PackError("This pack was made by a newer VSD Pad. Update VSD Pad and try again.")
    profile = read_json("profile.json") if "profile.json" in names else None
    if profile is not None:
        if not isinstance(profile.get("layouts"), list):
            raise PackError("profile.json in the pack has no pages.")
        profile = {"name": str(profile.get("name") or manifest.get("name") or "Shared")[:24],
                   **{k: profile[k] for k in layoutprofiles.SAVED_KEYS if k in profile}}
    images, themes, packs = {}, {}, {}
    for n, info in names.items():
        folder, _, base = n.partition("/")
        if folder == "images" and IMAGE_NAME_RE.match(base):
            if info.file_size > MAX_IMAGE:
                raise PackError(f"The picture {base} is too big.")
            images[base] = zf.read(info)
        elif folder in ("themes", "iconpacks") and base.endswith(".zip") and THEME_ID_RE.match(base[:-4]):
            (themes if folder == "themes" else packs)[base[:-4]] = zf.read(info)
    return manifest, profile, images, themes, packs


def inspect(app, blob):
    """What's in a pack, and what of it you already have."""
    manifest, profile, images, themes, packs = _open(blob)
    have_themes = {t["id"] for t in list_themes()}
    have_packs = {p["id"] for p in iconpacks.list_packs()}
    out = {"name": str(manifest.get("name") or "")[:60], "created": str(manifest.get("created") or "")[:20], "profile": None,
           "images": len(images), "themes": [{"id": t, "have": t in have_themes} for t in sorted(themes)],
           "iconpacks": [{"id": p, "have": p in have_packs} for p in sorted(packs)]}
    if profile:
        out["profile"] = {"name": profile["name"], "pages": sum(len(l.get("pages") or []) for l in profile["layouts"]),
                          "sizes": sorted({f"{l.get('cols')}×{l.get('rows')}" for l in profile["layouts"]})}
    return out


def _check_image(name, blob):
    try:
        with Image.open(io.BytesIO(blob)) as im:
            fmt = im.format
            if im.width * im.height > 40_000_000:
                raise PackError(f"The picture {name} is too large.")
            im.verify()
    except (OSError, SyntaxError):
        raise PackError(f"The picture {name} in the pack is damaged.") from None
    if fmt != FORMATS[name.rsplit(".", 1)[1]]:
        raise PackError(f"The picture {name} in the pack isn't what its name says.")


def install(app, blob, profile=True, use_now=False, replace=False):
    """Bring a pack in. profile: add its pages as a new profile; use_now: and switch to it; replace: overwrite
    themes / icon packs you already have with the pack's copies. Returns what was done."""
    manifest, prof, images, themes, packs = _open(blob)
    for name, data in images.items():
        _check_image(name, data)
    done = {"profile": None, "images": 0, "themes": [], "iconpacks": [], "skipped": []}
    have_themes = {t["id"] for t in list_themes()}
    for tid, data in sorted(themes.items()):
        if tid in have_themes and not replace:
            done["skipped"].append(f"theme {tid} (you have it)")
            continue
        done["themes"].append(themes_io.import_zip(data, replace=replace))
    have_packs = {p["id"]: p for p in iconpacks.list_packs()}
    for pid, data in sorted(packs.items()):
        mine = have_packs.get(pid)
        if mine and (mine["builtin"] or not replace):
            done["skipped"].append(f"icon pack {pid} (you have it)")
            continue
        got = iconpacks.import_zip(data, pack_id=pid, replace=bool(mine))
        done["iconpacks"].append(got.get("id", pid) if isinstance(got, dict) else pid)
    for name, data in images.items():
        dest = os.path.join(app.images_dir, name)
        if not os.path.exists(dest):
            with open(dest, "wb") as f:
                f.write(data)
            done["images"] += 1
    if profile and prof:
        box = {}

        def apply(cfg):
            pid = layoutprofiles.add(cfg, prof["name"], start="data", saved=prof)
            box["name"] = next(p["name"] for p in cfg["layout_profiles"]["list"] if p["id"] == pid)
            if use_now:
                layoutprofiles.switch(cfg, pid)
        app.store.update(apply)
        done["profile"] = box["name"]
    return done
