"""Starter pages for a pad with a different grid size (a 6-key Stream Deck Mini next to a 15-key M18, say).

Instead of a blank page, the new pad gets a copy of the main pages packed onto its grid: the keys of each page keep their
order (left to right, top to bottom) and gaps close up. A page with more keys than the pad has is split over several
pages, and the last key of each part moves on to the next part:
  - normal pages become "Name", "Name 2", ... and the last key is "Next page";
  - folders become a chain of folders: "More" opens the next part, the last part's spare key is "Back".
A pad at least as big as the main grid keeps every key where it was.
"Open folder / page" keys are pointed at the copies, so folders keep working.
"""
from .migrate import new_id
from .schema import MAX_PAGES

MAX_NAME = 24


def _nav_key(label, icon, action, params=None):
    return {"icon": icon, "label": label, "tap": [{"plugin": "pages", "action": action, "params": params or {}}]}


def _part_name(name, n):
    if n == 1:
        return name
    suffix = f" {n}"
    return name[:MAX_NAME - len(suffix)] + suffix


def fit_pages(pages, cols, rows, used_ids=(), src_cols=None, src_rows=None):
    """Copies of `pages` that fit a cols x rows grid. used_ids: page ids already taken (they're unique config-wide).
    When the source grid (src_cols x src_rows) fits inside the new one, keys keep their row and column instead."""
    n = cols * rows
    if src_cols and src_rows and src_cols <= cols and src_rows <= rows:
        return _place_same(pages, cols, src_cols, used_ids)
    used = set(used_ids)
    plan, id_map = [], {}
    for pg in pages:
        keys = [k for _slot, k in sorted(pg.get("keys", {}).items(), key=lambda kv: int(kv[0]))]
        if len(keys) <= n or n < 2:
            chunks = [keys[:n]]
        else:
            per = n - 1
            chunks = [keys[i:i + per] for i in range(0, len(keys), per)]
        ids = []
        for _ in chunks:
            ids.append(new_id(used))
            used.add(ids[-1])
        id_map[pg["id"]] = ids[0]
        plan.append((pg, chunks, ids))

    out = []
    for pg, chunks, ids in plan:
        folder = bool(pg.get("folder"))
        for i, chunk in enumerate(chunks):
            keys = [_remap(k, id_map) for k in chunk]
            if len(chunks) > 1:
                if not folder:
                    keys.append(_nav_key("Next", "→", "next"))
                elif i + 1 < len(chunks):
                    keys.append(_nav_key("More", "→", "open", {"page": ids[i + 1]}))
                else:
                    keys.append(_nav_key("Back", "←", "back"))
            new = {k: v for k, v in pg.items() if k not in ("id", "name", "keys")}
            new.update(id=ids[i], name=_part_name(pg.get("name") or "Page", i + 1),
                       keys={str(s + 1): k for s, k in enumerate(keys)})
            out.append(new)
            if len(out) >= MAX_PAGES:
                return out
    return out


def _place_same(pages, cols, src_cols, used_ids):
    used, id_map = set(used_ids), {}
    for pg in pages:
        id_map[pg["id"]] = new_id(used)
        used.add(id_map[pg["id"]])
    out = []
    for pg in pages[:MAX_PAGES]:
        keys = {}
        for slot, k in pg.get("keys", {}).items():
            r, c = divmod(int(slot) - 1, src_cols)
            keys[str(r * cols + c + 1)] = _remap(k, id_map)
        new = {k: v for k, v in pg.items() if k not in ("id", "keys")}
        new.update(id=id_map[pg["id"]], keys=keys)
        out.append(new)
    return out


def _remap(key, id_map):
    """A deep-enough copy of a key with page/folder links pointed at the copied pages."""
    import copy
    key = copy.deepcopy(key)
    for g in ("tap", "hold", "double"):
        for step in key.get(g) or []:
            if step.get("plugin") == "pages" and step.get("action") == "open":
                params = step.get("params") or {}
                if params.get("page") in id_map:
                    params["page"] = id_map[params["page"]]
    return key


def starter_layout(layouts, lid, name, cols, rows):
    """A new layout for a cols x rows pad, seeded from the first layout (the main pages)."""
    used = {p["id"] for lay in layouts for p in lay["pages"]}
    src = layouts[0] if layouts else None
    pages = fit_pages(src["pages"], cols, rows, used, src["cols"], src["rows"]) if src else []
    if not pages:
        pages = [{"id": new_id(used), "name": "Main", "folder": False, "keys": {}}]
    return {"id": lid, "name": name[:32], "cols": cols, "rows": rows, "buttons": {}, "pages": pages}
