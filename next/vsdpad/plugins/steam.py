"""Steam games: launch installed games from keys, with their cover art from your own Steam library cache."""
import glob, os, re, sys, time, webbrowser

from .base import Plugin

SKIP = re.compile(r"Steamworks Common|Proton|Steam Linux Runtime|Redistributable|SteamVR", re.I)


def parse_vdf(text):
    """Minimal Valve KeyValues (text) parser -> nested dicts."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root, stack, key = {}, [], None
    cur = root
    for quoted, brace in tokens:
        if brace == "{":
            new = {}
            cur[key if key is not None else ""] = new
            stack.append(cur)
            cur, key = new, None
        elif brace == "}":
            cur = stack.pop() if stack else root
            key = None
        else:
            val = quoted.replace('\\\\', '\\').replace('\\"', '"')
            if key is None:
                key = val
            else:
                cur[key] = val
                key = None
    return root


def steam_dir():
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                path = winreg.QueryValueEx(k, "SteamPath")[0]
                if path and os.path.isdir(path):
                    return os.path.normpath(path)
        except OSError:
            pass
        for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
            if os.path.isdir(p):
                return p
        return None
    for p in ("~/.steam/steam", "~/.local/share/Steam"):
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            return p
    return None


def library_paths(root):
    paths = [root]
    try:
        with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            data = parse_vdf(f.read())
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        for v in folders.values():
            p = v.get("path") if isinstance(v, dict) else v
            if isinstance(p, str) and os.path.isdir(p) and os.path.normcase(p) not in map(os.path.normcase, paths):
                paths.append(p)
    except OSError:
        pass
    return paths


def installed_games(root):
    games = {}
    for lib in library_paths(root):
        for mf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            try:
                with open(mf, encoding="utf-8", errors="replace") as f:
                    st = parse_vdf(f.read()).get("AppState", {})
            except OSError:
                continue
            appid, name = st.get("appid"), st.get("name")
            if appid and appid.isdigit() and name and not SKIP.search(name):
                games[appid] = {"appid": appid, "name": name, "last_played": int(st.get("LastPlayed", "0") or 0)}
    return sorted(games.values(), key=lambda g: g["name"].lower())


def find_art(root, appid):
    """Cover art for a game from Steam's own cache (old flat layout and the newer per-game folders)."""
    cache = os.path.join(root, "appcache", "librarycache")
    for pattern in (f"{appid}_library_600x900.jpg", f"{appid}/library_600x900.jpg", f"{appid}/*/library_600x900.jpg",
                    f"{appid}_header.jpg", f"{appid}/header.jpg", f"{appid}/*/header.jpg"):
        hits = glob.glob(os.path.join(cache, pattern))
        if hits:
            return hits[0]
    return None


class Steam(Plugin):
    id = "steam"
    name = "Steam games"
    description = "Launch your installed Steam games from keys, with their cover art."
    buttons = [{"id": "test", "label": "Find my games", "api": "test"}]
    actions = {"launch": {"label": "Launch a game", "params": [
        {"key": "appid", "label": "Game", "kind": "choice", "api": "games"}]}}

    def __init__(self):
        self._cache = (0, None, [])

    def _games(self):
        t, root, games = self._cache
        if time.time() - t > 60:
            root = steam_dir()
            games = installed_games(root) if root else []
            self._cache = (time.time(), root, games)
        return root, games

    def run(self, action, params, ctx):
        if action != "launch":
            raise ValueError(f"unknown steam action {action!r}")
        appid = str(params.get("appid", ""))
        if not appid.isdigit():
            raise ValueError("Choose a game first.")
        webbrowser.open(f"steam://rungameid/{appid}")

    def api(self, name, payload, ctx):
        root, games = self._games()
        if name == "test":
            if not root:
                return {"ok": False, "message": "Steam isn't installed (or I couldn't find it)."}
            return {"ok": True, "message": f"Found {len(games)} installed games."}
        if name == "games":
            return [{"value": g["appid"], "label": g["name"], "image": f"steam:{g['appid']}",
                     "last_played": g["last_played"]} for g in games]
        return super().api(name, payload, ctx)

    def image(self, ref):
        if not ref.isdigit():
            return None
        root, _ = self._games()
        return find_art(root, ref) if root else None


PLUGIN = Steam()
