"""Tests for page/folder navigation (pagestate), the Pages plugin and the v3 -> v4 config migration.
Run:  python tests/test_pages.py
"""
import copy, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pagestate  # noqa: E402
import plugins  # noqa: E402
import store as st  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name, detail)


def raises(fn, contains):
    try:
        fn()
    except Exception as e:
        return contains.lower() in str(e).lower()
    return False


# Layout: 0 Main, 1 Lights(folder), 2 Media, 3 Hue(folder, opened from Lights), 4 Apps
PAGES = [{"id": "main", "name": "Main"}, {"id": "lights", "name": "Lights", "folder": True},
         {"id": "media", "name": "Media"}, {"id": "hue", "name": "Hue", "folder": True},
         {"id": "apps", "name": "Apps"}]


def fresh():
    s = pagestate.PageState()
    s.configure(PAGES)
    return s


s = fresh()
check("starts on the first page", s.current == 0 and s.depth() == 0)
s.open("lights")
check("open a folder", s.current == 1 and s.history == [0])
s.open("hue")
check("folders nest", s.current == 3 and s.history == [0, 1])
s.back()
check("back goes up one level", s.current == 1 and s.history == [0])
s.back()
check("back again returns to the start page", s.current == 0 and s.history == [])
s.back()
check("back with no trail stays on the first top-level page", s.current == 0 and s.history == [])

# Next/Previous skip folders
s = fresh()
seq = []
for _ in range(4):
    seq.append(s.step(1))
check("next cycles only top-level pages (0 -> 2 -> 4 -> 0 ...)", seq == [2, 4, 0, 2], seq)
seq = [s.step(-1) for _ in range(3)]
check("previous wraps backwards over top-level pages", seq == [0, 4, 2], seq)

# Next from inside a folder steps relative to where the folder was opened
s = fresh()
s.open("lights")             # opened from Main (0)
s.step(1)
check("next inside a folder moves on from the page it was opened from", s.current == 2 and s.history == [], (s.current, s.history))
s = fresh()
s.step(1); s.open("hue")     # Media(2) -> folder Hue
s.step(-1)
check("previous inside a folder uses the opening page", s.current == 0, s.current)

# goto jumps anywhere and clears the trail; wraps
s = fresh()
s.open("lights"); s.goto(4)
check("goto clears the folder trail", s.current == 4 and s.history == [])
s.goto(7)
check("goto wraps around", s.current == 2, s.current)

# bad folder id
s = fresh()
check("opening a deleted folder gives a readable error", raises(lambda: s.open("gone"), "no longer exists"))
check("a failed open changes nothing", s.current == 0 and s.history == [])

# version bumps drive the redraw
s = fresh(); v = s.version
s.open("lights"); check("open bumps version", s.version > v)
v = s.version; s.open("lights"); check("opening the page you're on is a no-op", s.version == v)

# reconfigure keeps position by id, prunes missing pages
s = fresh(); s.open("lights"); s.open("hue")
s.configure([PAGES[0], PAGES[3], PAGES[1]])   # reordered, others deleted
check("reconfigure keeps the current page by id", s.ids[s.current] == "hue", (s.ids, s.current))
check("trail follows pages by id", [s.ids[i] for i in s.history] == ["main", "lights"], s.history)
s.configure([PAGES[0]])                        # current page deleted
check("deleting the current page falls back to the first", s.current == 0 and s.history == [])
s = pagestate.PageState(); s.configure([{"id": "only", "name": "Only", "folder": False}])
check("a single page: next stays put", s.step(1) == 0)

# plugin wiring uses the shared STATE
plugins.load_plugins()
P = plugins.PLUGINS["pages"]
pagestate.STATE.configure(PAGES)
P.run("open", {"page": "lights"}, {}); check("plugin open", pagestate.STATE.current == 1)
P.run("back", {}, {}); check("plugin back", pagestate.STATE.current == 0)
P.run("next", {}, {}); check("plugin next skips folders", pagestate.STATE.current == 2)
P.run("goto", {"page": 4}, {}); check("plugin goto by number can reach a folder", pagestate.STATE.current == 3)
P.run("home", {}, {}); check("plugin home", pagestate.STATE.current == 0)
check("plugin goto out of range", raises(lambda: P.run("goto", {"page": 9}, {}), "no page 9"))
check("plugin open with no choice", raises(lambda: P.run("open", {}, {}), "no longer exists"))
d = P.describe()
check("open's picker is the GUI-side page list", d["actions"]["open"]["params"][0]["api"] == "@pages")

# ---- config migration v3 -> v4 and validation
v3 = {"version": 3, "brightness": 80, "round_buttons": [37, 48, 49], "round": {}, "icon_pack": "modern", "plugins": {},
      "pages": [{"name": "Main", "keys": {"1": {"label": "A"}}}, {"name": "Two", "keys": {}}]}
new, changed, frm = st.migrate(v3)
check("v3 -> v4 migrates", changed and frm == 3 and new["version"] == 4)
ids = [p["id"] for p in new["pages"]]
check("every page gets a unique id", len(set(ids)) == 2 and all(st.PAGE_ID_RE.match(i) for i in ids), ids)
check("pages default to not-a-folder", all(p["folder"] is False for p in new["pages"]))
check("keys are untouched", new["pages"][0]["keys"] == {"1": {"label": "A"}})
st.validate(new)
check("v4 config validates", True)
bad = copy.deepcopy(new); bad["pages"][1]["id"] = bad["pages"][0]["id"]
check("duplicate ids rejected", raises(lambda: st.validate(bad), "unique id"))
bad = copy.deepcopy(new); bad["pages"][0]["folder"] = True
check("first page can't be a folder", raises(lambda: st.validate(bad), "first page"))
bad = copy.deepcopy(new); bad["pages"][1]["folder"] = "yes"
check("folder must be a boolean", raises(lambda: st.validate(bad), "true or false"))
bad = copy.deepcopy(new); bad["pages"][1]["id"] = "../x"
check("bad id characters rejected", raises(lambda: st.validate(bad), "unique id"))
big = copy.deepcopy(new); big["pages"] = [{"id": f"p{i:03d}", "name": "x", "folder": i > 0, "keys": {}} for i in range(25)]
check("more than 24 pages rejected", raises(lambda: st.validate(big), "pages"))
ok24 = copy.deepcopy(new); ok24["pages"] = big["pages"][:24]; st.validate(ok24); check("24 pages allowed", True)

# real file round trip incl. backup naming
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "config.json"); json.dump(v3, open(path, "w"))
    store = st.Store(path)
    check("store migrated on load", store.get()["version"] == 4 and os.path.exists(path + ".v3.bak"))
    check("backup keeps the old format", json.load(open(path + ".v3.bak"))["version"] == 3)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
