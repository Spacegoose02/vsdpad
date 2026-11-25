"""Server + PC: edit your pads on a server (e.g. Unraid in Docker), the PC with the pads plugged in follows it.

The server (``run.py --server``) runs the normal settings page for the whole home network, behind a password,
and keeps the layout, pictures, themes and icon packs. It has no pads: USB stays on the PC.

The PC (``run.py --connect http://tower:8765 --password ...`` once; afterwards plain ``run.py``) keeps a copy of
everything and drives the pads from that copy, so it keeps working when the server is off. It asks the server for
changes with a long poll (an answer comes the moment you press Save), downloads only files that changed, and every
couple of seconds tells the server which pads are plugged in and what they show. Pressing a key in the server's
settings page presses it on the PC, so Windows actions (media keys, apps, typing) happen where they should.

Endpoints on the server, all behind HTTP Basic auth (any user name, the server password):
  GET  /sync/hello                      -> {"ok", "version"}            (checks the password)
  POST /sync/poll   {"agent", "have"}   -> {"state", "commands", ["config", "files"]}   waits up to POLL_S
  POST /sync/status {"agent", "status"} -> {"ok"}                       the PC's pads (shown in the settings page)
  GET  /sync/file?root=&path=           -> bytes                        one picture / theme / icon pack file
  POST /sync/file   {"root", "path", "data"}                            (--upload-layout) send a file
  POST /sync/upload {"config"}                                          (--upload-layout) replace the layout

The link to the server is kept in data/sync.json ({"url", "password"}, the password protected like the other
tokens), not in config.json, because config.json is replaced by the server's copy.
"""
import base64, collections, copy, hashlib, json, os, re, socket, threading, time, urllib.error, urllib.parse, urllib.request

from . import __version__, iconpacks, render
from .log import LOG

POLL_S = 25              # the server answers a poll at the latest after this long
STATUS_S = 2             # the PC reports its pads this often
ONLINE_S = 12            # a PC that hasn't reported for this long is shown as gone
MAX_FILE = 30_000_000
LINK_FILE = "sync.json"
NOTE_PREFIX = "Layout from "   # the PC's own note about the server, not sent back to the server
PATH_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}(/[A-Za-z0-9_.-]{1,96}){0,3}$")
FILE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".json", ".txt")   # pictures and pack descriptions only


# ---- what gets synced: the config plus three folders of files ------------------------------------------
def roots(app):
    """name -> folder. Pictures, themes and your own icon packs (built-in icon packs ship with the app)."""
    out = {"images": app.images_dir, "themes": render.THEMES_DIR}
    if iconpacks.user_dir():
        out["iconpacks"] = iconpacks.user_dir()
    return out


def safe_path(root_dir, rel):
    """The file for a synced relative path, or None if the path could leave the folder."""
    if not isinstance(rel, str) or not PATH_RE.match(rel) or not rel.lower().endswith(FILE_TYPES) \
            or any(p in (".", "..") or p.startswith(".") for p in rel.split("/")):
        return None
    return os.path.join(root_dir, *rel.split("/"))


_digests, _digest_lock = {}, threading.Lock()


def _digest(path, st):
    key = (path, st.st_size, st.st_mtime_ns)
    with _digest_lock:
        d = _digests.get(key)
    if d is None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        d = h.hexdigest()[:32]
        with _digest_lock:
            if len(_digests) > 20000:
                _digests.clear()
            _digests[key] = d
    return d


def manifest(app):
    """{root: {relative path: sha}} of every synced file."""
    out = {}
    for name, base in roots(app).items():
        files = {}
        if os.path.isdir(base):
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if not d.startswith((".", "__"))]
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, base).replace(os.sep, "/")
                    if safe_path(base, rel) is None:
                        continue
                    try:
                        st = os.stat(full)
                        if st.st_size <= MAX_FILE:
                            files[rel] = _digest(full, st)
                    except OSError:
                        continue
        out[name] = files
    return out


def config_hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def write_file(app, root, rel, data):
    base = roots(app).get(root)
    path = safe_path(base, rel) if base else None
    if path is None:
        raise ValueError("bad file path")
    if len(data) > MAX_FILE:
        raise ValueError("file too big")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".sync-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def files_changed():
    """Forget cached icon packs after their files changed (themes and pictures are re-read by mtime)."""
    iconpacks.set_user_dir(iconpacks.user_dir())


# ---- server side -----------------------------------------------------------------------------------------
class Hub:
    """Runs on the server: hands out the layout, remembers which PCs are connected and what their pads show."""

    def __init__(self, app):
        self.app = app
        self._cond = threading.Condition()
        self.agents = {}   # name -> {"seen", "status", "queue", "address"}
        app.store.on_change(self._changed)

    def _changed(self):
        with self._cond:
            self._cond.notify_all()

    def _agent(self, name):
        name = str(name or "PC")[:64]
        a = self.agents.get(name)
        if a is None:
            a = self.agents[name] = {"seen": 0.0, "status": {}, "queue": collections.deque(maxlen=50), "address": ""}
        return a

    def online(self):
        now = time.monotonic()
        return {n: a for n, a in self.agents.items() if now - a["seen"] < ONLINE_S}

    def state(self):
        cfg = self.app.store.get()
        files = manifest(self.app)
        st = hashlib.sha256((config_hash(cfg) + json.dumps(files, sort_keys=True)).encode()).hexdigest()[:32]
        return st, cfg, files

    def poll(self, body, address=""):
        have = body.get("have")
        try:
            timeout = max(0.0, min(float(body.get("wait", POLL_S)), POLL_S))
        except (TypeError, ValueError):
            timeout = POLL_S
        with self._cond:
            agent = self._agent(body.get("agent"))
            agent["seen"], agent["address"] = time.monotonic(), address
        deadline = time.monotonic() + timeout
        while True:
            version = self.app.store.version
            st, cfg, files = self.state()
            with self._cond:
                cmds = list(agent["queue"])
                agent["queue"].clear()
                agent["seen"] = time.monotonic()
                left = deadline - time.monotonic()
                if st != have or cmds or left <= 0:
                    break
                if self.app.store.version == version and not agent["queue"]:
                    self._cond.wait(min(5.0, left))   # wakes on Save or a key press; re-checks files every 5 s
        out = {"state": st, "commands": cmds}
        if st != have:
            out["config"], out["files"] = cfg, files
        return out

    def report(self, body, address=""):
        status = body.get("status") if isinstance(body.get("status"), dict) else {}
        devices = [d for d in status.get("devices") or [] if isinstance(d, dict) and isinstance(d.get("id"), str)][:16]
        with self._cond:
            agent = self._agent(body.get("agent"))
            agent["seen"], agent["address"] = time.monotonic(), address
            agent["status"] = {"devices": devices, "notes": [str(n)[:200] for n in status.get("notes") or []][:10],
                               "errors": [e for e in status.get("errors") or [] if isinstance(e, dict)][-5:]}
        from .devices.base import DeviceInfo
        for d in devices:   # a pad seen for the first time gets its pages, here on the server where you edit them
            try:
                cols, rows = int(d.get("cols", 5)), int(d.get("rows", 3))
                if not (1 <= cols <= 8 and 1 <= rows <= 4):
                    continue
                buttons = [int(b) for b in d.get("buttons") or []][:8]
                self.app.ensure_device(DeviceInfo(id=d["id"][:40], kind="remote", model=str(d.get("model") or "Pad")[:40],
                                                  cols=cols, rows=rows, key_px=72, buttons=buttons))
            except Exception as e:
                LOG.warning("couldn't add the pad %r from %s: %s", d.get("id"), address, e)
        return {"ok": True}

    def send(self, device_id, command):
        """Queue a command for the PC that has this pad (or the PC seen last). False if no PC is connected."""
        online = self.online()
        if not online:
            return False
        name = next((n for n, a in online.items() if any(d.get("id") == device_id for d in a["status"].get("devices", []))), None)
        if name is None:
            name = max(online, key=lambda n: online[n]["seen"])
        with self._cond:
            self.agents[name]["queue"].append(command)
            self._cond.notify_all()
        return True

    def status(self, local):
        """The settings page's status: pads come from the connected PCs."""
        online = self.online()
        devices, notes, errors = [], list(local.get("notes") or []), list(local.get("errors") or [])
        for name, a in online.items():
            for d in a["status"].get("devices", []):
                devices.append({**d, "agent": name})
            notes += a["status"].get("notes", [])
            errors += a["status"].get("errors", [])
        if not online:
            notes.append("No PC is connected. On the PC with the pad: VSD Pad → Settings → VSD Pad server → Find my server")
        errors.sort(key=lambda e: e.get("time") or 0)
        return {**local, "devices": devices, "notes": notes, "errors": errors[-5:], "server": True,
                "agents": [{"name": n, "address": a["address"]} for n, a in online.items()]}

    def upload(self, body):
        cfg = body.get("config")
        if not isinstance(cfg, dict):
            raise ValueError("missing config")
        self.app.store.import_({"vsdpad_export": 1, "config": cfg})
        return {"ok": True}

    def receive_file(self, body):
        try:
            data = base64.b64decode(str(body.get("data", "")), validate=True)
        except ValueError:
            raise ValueError("bad file data") from None
        write_file(self.app, str(body.get("root", "")), body.get("path"), data)
        files_changed()
        return {"ok": True}

    def file(self, root, rel):
        base = roots(self.app).get(root)
        path = safe_path(base, rel) if base else None
        if path is None or not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()


# ---- PC side ----------------------------------------------------------------------------------------------
def link_path(data_dir):
    return os.path.join(data_dir, LINK_FILE)


def normalize_url(url):
    url = str(url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("The server address should look like http://tower:8765")
    if u.port is None and u.scheme == "http":
        url += ":8765"
    return url


def load_link(data_dir):
    """(url, password) of the server this PC follows, or None."""
    from . import secret
    try:
        with open(link_path(data_dir), encoding="utf-8") as f:
            d = json.load(f)
        return d["url"], secret.unprotect(d.get("password", ""))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_link(data_dir, url, password):
    from . import secret
    os.makedirs(data_dir, exist_ok=True)
    tmp = link_path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url, "password": secret.protect(password)}, f, indent=2)
    os.replace(tmp, link_path(data_dir))


def remove_link(data_dir):
    try:
        os.remove(link_path(data_dir))
        return True
    except OSError:
        return False


def auth_headers(password):
    return {"Authorization": "Basic " + base64.b64encode(f"vsdpad:{password}".encode()).decode()}


class ServerError(OSError):
    pass


class Client:
    """Talks to the server: plain urllib, JSON in and out."""

    def __init__(self, url, password, opener=None):
        self.url, self.password = url.rstrip("/"), password
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # home network: no proxy

    def call(self, path, body=None, timeout=10, raw=False):
        data = None if body is None else json.dumps(body).encode()
        headers = auth_headers(self.password)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with self._open(req, timeout=timeout) as r:
                blob = r.read(MAX_FILE + 1)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ServerError("the server says the password is wrong") from None
            try:
                msg = json.loads(e.read()).get("error") or e.reason
            except Exception:
                msg = e.reason
            raise ServerError(f"server error {e.code}: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ServerError(f"can't reach the server at {self.url} ({getattr(e, 'reason', e)})") from None
        if len(blob) > MAX_FILE:
            raise ServerError("the server sent too much data")
        return blob if raw else json.loads(blob or b"{}")


def agent_name():
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC")[:64]


class Follower:
    """Runs on the PC: keeps the local copy in step with the server and reports the pads."""

    def __init__(self, app, client, settings_server=None):
        self.app, self.client, self.settings = app, client, settings_server
        self.name = agent_name()
        self.have = None            # server state we have
        self.applied = None         # hash of the server config we applied last
        self.problem = ""           # shown in the local settings page while the server can't be reached
        self.last_ok = 0.0
        self._stop = threading.Event()
        self._first = True

    @property
    def url(self):
        return self.client.url

    def start(self):
        threading.Thread(target=self._poll_loop, name="sync-poll", daemon=True).start()
        threading.Thread(target=self._status_loop, name="sync-status", daemon=True).start()

    def stop(self):
        self._stop.set()

    # ---- following the server ----
    def _poll_loop(self):
        backoff = 2
        while not self._stop.is_set():
            try:
                self.poll_once()
                backoff = 2
            except Exception as e:
                msg = str(e) or type(e).__name__
                if msg != self.problem:
                    LOG.warning("sync: %s", msg)
                self.problem = msg
                if self._stop.wait(backoff):
                    break
                backoff = min(30, backoff * 2)

    def poll_once(self, wait=POLL_S):
        """Ask for changes; the server answers at once if there are any, else after up to `wait` seconds."""
        r = self.client.call("/sync/poll", {"agent": self.name, "have": self.have, "wait": wait}, timeout=wait + 15)
        if self._stop.is_set():   # disconnected while the server held the poll: don't touch the layout any more
            return r
        if "config" in r:
            self.apply(r["config"], r.get("files") or {})
        self.have = r.get("state", self.have)
        if self.problem:
            LOG.info("sync: connected to %s again", self.url)
        self.problem, self.last_ok = "", time.time()
        for cmd in r.get("commands") or []:
            self.run_command(cmd)
        return r

    def apply(self, cfg, files):
        got = 0
        mine = manifest(self.app)
        for root, entries in files.items():
            if root not in mine or not isinstance(entries, dict):
                continue
            for rel, sha in entries.items():
                if mine[root].get(rel) == sha:
                    continue
                try:
                    data = self.client.call("/sync/file?" + urllib.parse.urlencode({"root": root, "path": rel}), raw=True, timeout=60)
                    write_file(self.app, root, rel, data)
                    got += 1
                except (ValueError, OSError) as e:
                    LOG.warning("sync: couldn't get %s/%s: %s", root, rel, e)
        if got:
            files_changed()
            LOG.info("sync: downloaded %d file(s)", got)
        h = config_hash(cfg)
        if h != self.applied:
            self.app.store.replace(cfg, snapshot=self._first)   # the layout from before the first sync stays in the history
            self.applied, self._first = h, False
            LOG.info("sync: layout updated from %s", self.url)

    def run_command(self, cmd):
        if not isinstance(cmd, dict) or self.settings is None:
            return
        try:
            if cmd.get("do") == "run":
                self.settings.run(cmd)
            elif cmd.get("do") == "goto":
                self.settings.goto(cmd)
        except Exception as e:
            LOG.warning("sync: %s from the server failed: %s", cmd.get("do"), e)
            self.app.report_error(cmd.get("device"), str(e))

    # ---- reporting the pads ----
    def _status_loop(self):
        while not self._stop.wait(STATUS_S):
            try:
                st = self.app.status()
                st = {k: st.get(k) for k in ("devices", "notes", "errors")}
                st["notes"] = [n for n in st.get("notes") or [] if not n.startswith(NOTE_PREFIX)]
                self.client.call("/sync/status", {"agent": self.name, "status": st}, timeout=8)
            except Exception:
                pass   # the poll loop logs connection trouble

    def info(self):
        return {"url": self.url, "connected": not self.problem and bool(self.last_ok), "problem": self.problem}

    # ---- one-off: send this PC's layout to the server ----
    def upload_layout(self):
        theirs = self.client.call("/sync/poll", {"agent": self.name, "have": "upload", "wait": 0}, timeout=30).get("files") or {}
        sent = 0
        for root, entries in manifest(self.app).items():
            if root not in theirs:
                continue
            base = roots(self.app)[root]
            for rel, sha in entries.items():
                if theirs[root].get(rel) == sha:
                    continue
                with open(safe_path(base, rel), "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                self.client.call("/sync/file", {"root": root, "path": rel, "data": data}, timeout=60)
                sent += 1
        self.client.call("/sync/upload", {"config": copy.deepcopy(self.app.store.get())}, timeout=30)
        return sent


# ---- connecting from the settings page (no command line needed) ----------------------------------------
DEFAULT_PORT = 8765


def server_name():
    return (os.environ.get("VSDPAD_NAME") or "VSD Pad server")[:64]


def probe(url, timeout=1.5):
    """{"url", "name", "version"} if a VSD Pad server answers at url, else None. Needs no password."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/healthz", headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            d = json.loads(r.read(4096))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("app") != "vsdpad-server":
        return None
    return {"url": url.rstrip("/"), "name": str(d.get("name") or "VSD Pad server")[:64], "version": str(d.get("version", ""))[:32]}


def discover(port=DEFAULT_PORT, timeout=0.8, addresses=None):
    """Find VSD Pad servers on the home network: try every address next to this PC's (x.y.z.1-254) and a few
    usual Unraid names. Takes a couple of seconds. Returns [{"url", "name", "version"}]."""
    from concurrent.futures import ThreadPoolExecutor
    if addresses is None:
        from .remote import lan_addresses
        addresses = lan_addresses()
    urls = [f"http://{h}:{port}" for h in ("tower", "tower.local")]
    for ip in addresses:
        parts = ip.split(".")
        if len(parts) == 4:
            urls += [f"http://{'.'.join(parts[:3])}.{n}:{port}" for n in range(1, 255)]
    found, seen = [], set()
    with ThreadPoolExecutor(max_workers=64) as pool:
        for r in pool.map(lambda u: probe(u, timeout), dict.fromkeys(urls)):
            if r and r["url"] not in seen:
                seen.add(r["url"])
                found.append(r)
    names = {"http://tower", "http://tower.local"}
    # the same server by name and by address: keep the address (works even where names don't)
    if any(not f["url"].rsplit(":", 1)[0] in names for f in found):
        found = [f for f in found if f["url"].rsplit(":", 1)[0] not in names] or found
    return found


def connect(app, url, password, settings_server=None, upload=False):
    """Follow a server from now on (checks the password first). Returns the server's address."""
    url = normalize_url(url)
    client = Client(url, password)
    client.call("/sync/hello")          # raises ServerError with a readable message
    disconnect(app, forget=False)
    save_link(app.data_dir, url, password)
    f = Follower(app, client, settings_server)
    if upload:
        f.upload_layout()
    app.follower = f
    f.start()
    LOG.info("following the server at %s", url)
    return url


def disconnect(app, forget=True):
    """Stop following (this PC keeps its last copy of the layout)."""
    f, app.follower = app.follower, None
    if f:
        f.stop()
    if forget:
        remove_link(app.data_dir)
        if f:
            LOG.info("stopped following %s", f.url)
