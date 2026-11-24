"""Updates for the installed app (VSDPad.exe).

Every push to the release branch builds VSDPad-Setup.exe on GitHub and publishes it as a release tagged build-<n> (see
.github/workflows/windows-build.yml). The app looks for a newer build in one of two places (Plugins -> Updates):

  * your VSD Pad server (the Unraid/Docker server), when this PC is linked to one (data/sync.json, see sync.py). The
    server fetches the releases from GitHub itself, so the PC needs no GitHub token. It answers
    GET <server>/sync/updates/latest with the same JSON as GitHub's releases/latest and serves the installer at
    /sync/updates/asset/<name> (see update_mirror.py), with the same password the config sync uses.
  * GitHub directly. The repository is private, so this needs a read-only token (stored encrypted like other tokens).

When a newer build is out, the app downloads the installer to %LOCALAPPDATA%\\VSD Pad\\updates, checks its size and
SHA-256, then quits. Only after it has quit and let go of the pad does it start the installer, directly (no hidden
command windows: that pattern is what antivirus programs look for). The installer updates quietly and starts it again.
Your layout lives in %APPDATA%\\VSD Pad and is never touched by an update.
"""
import glob, hashlib, json, os, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request

from .log import LOG

REPO = "Spacegoose02/vsdpad"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
SERVER_LATEST = "/sync/updates/latest"
FIRST_CHECK_S = 120           # after starting
EVERY_S = 6 * 3600            # then this often
IDLE_S = 600                  # only install by itself when no pad key was pressed for this long
TIMEOUT = 20
FROZEN = getattr(sys, "frozen", False)
INSTALL_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/NORESTART", "/SP-"]


def current_build():
    """Build number baked in by the GitHub build (0 = running from source)."""
    try:
        from ._build import BUILD
        return int(BUILD)
    except (ImportError, ValueError):
        return 0


def _build_of(tag):
    try:
        return int(str(tag).rsplit("-", 1)[-1])
    except ValueError:
        return 0


def download_dir():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(base, "VSD Pad", "updates")


class Updater:
    def __init__(self, app, quit_app=None, opener=urllib.request.urlopen, folder=None):
        self.app, self.quit_app, self._open = app, quit_app, opener
        self.folder = folder or download_dir()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.pending = None     # installer to start once the app has quit
        self.state = {"current": current_build(), "latest": None, "available": False, "checked": None, "error": None,
                      "installing": False, "source": None}

    # ---- settings -----------------------------------------------------------------------
    def _settings(self):
        return self.app.store.plugin_settings("updates")

    def token(self):
        return (self._settings().get("token") or "").strip()

    def _link(self):
        """(url, password) of the VSD Pad server this PC syncs from, or None."""
        try:
            from . import sync
        except ImportError:
            return None
        try:
            return sync.load_link(self.app.data_dir)
        except Exception as e:   # an unreadable link file shouldn't stop updates from GitHub
            LOG.warning("update check: couldn't read the server link (%s)", e)
            return None

    def server(self):
        """The linked VSD Pad server's address, or "" to use GitHub ("Updates from" = github forces GitHub)."""
        if (self._settings().get("from") or "").strip().lower() == "github":
            return ""
        link = self._link()
        return str(link[0]).strip().rstrip("/") if link and link[0] else ""

    def auto(self):
        return (self._settings().get("auto") or "yes").strip().lower() not in ("no", "off", "false", "0")

    def configured(self):
        return bool(self.server() or self.token())

    def source(self):
        return "server" if self.server() else "github"

    # ---- requests -----------------------------------------------------------------------
    def _request(self, url, accept):
        headers = {"Accept": accept, "User-Agent": "vsdpad-updater"}
        req = urllib.request.Request(url, headers=headers)
        if self.source() == "server":
            from . import sync
            for k, v in sync.auth_headers(self._link()[1]).items():
                req.add_unredirected_header(k, v)
        else:
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
            if self.token():
                # not passed on when GitHub redirects the download to its file storage (which would refuse it)
                req.add_unredirected_header("Authorization", "Bearer " + self.token())
        return self._open(req, timeout=TIMEOUT)

    def _latest_url(self):
        return self.server() + SERVER_LATEST if self.server() else LATEST

    def _error(self, e):
        if self.source() == "server":
            where = self.server()
            if isinstance(e, urllib.error.HTTPError):
                return {401: f"The VSD Pad server at {where} refused this PC's password. Link it again.",
                        404: f"The VSD Pad server at {where} doesn't offer updates yet (update it, or check it has "
                             "a GitHub token).",
                        502: f"The VSD Pad server at {where} couldn't reach GitHub.",
                        }.get(e.code, f"The VSD Pad server said {e.code} {e.reason}.")
            return f"Couldn't reach the VSD Pad server at {where} ({getattr(e, 'reason', e)})."
        if isinstance(e, urllib.error.HTTPError):
            return {401: "GitHub refused the token (it may have expired). Make a new one and paste it in Plugins → Updates.",
                    403: "GitHub refused the token. Check it can read the vsdpad repository (Contents: Read-only).",
                    404: "No update found: either nothing is published yet, or the token can't see the vsdpad repository."
                    }.get(e.code, f"GitHub said {e.code} {e.reason}.")
        return f"Couldn't reach GitHub ({getattr(e, 'reason', e)})."

    def check(self):
        """Look for a newer build. Returns the state dict; raises ValueError with a readable message."""
        if not self.configured():
            raise ValueError("Link this PC to your VSD Pad server, or paste a GitHub token in Plugins → Updates, first.")
        src = self.source()
        try:
            with self._request(self._latest_url(), "application/vnd.github+json") as r:
                rel = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            msg = self._error(e) if not isinstance(e, ValueError) else "The update information was unreadable."
            self._set(error=msg, checked=time.time(), source=src)
            raise ValueError(msg) from None
        latest = _build_of(rel.get("tag_name"))
        asset = next((a for a in rel.get("assets", []) if str(a.get("name", "")).startswith("VSDPad-Setup")
                      and str(a.get("name", "")).endswith(".exe")), None)
        if asset and src == "server":
            # a server may give a path relative to itself
            asset = dict(asset, url=urllib.parse.urljoin(self.server() + "/", str(asset.get("url") or "")))
        self._set(latest=latest, checked=time.time(), error=None, asset=asset, notes=(rel.get("body") or "")[:2000],
                  available=bool(asset) and latest > self.state["current"], source=src)
        return self.public()

    def public(self):
        with self._lock:
            return {k: v for k, v in self.state.items() if k != "asset"}

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def download(self, asset, folder=None):
        folder = folder or self.folder
        os.makedirs(folder, exist_ok=True)
        for old in glob.glob(os.path.join(folder, "VSDPad-Setup*.exe")):   # earlier updates
            try:
                os.remove(old)
            except OSError:
                pass
        name = os.path.basename(str(asset["name"]))
        if not (name.startswith("VSDPad-Setup") and name.endswith(".exe")):
            raise ValueError("The update isn't a VSD Pad installer, so it wasn't downloaded.")
        want = str(asset.get("digest") or "")
        if self.source() == "server" and not want.startswith("sha256:"):
            raise ValueError("The VSD Pad server didn't send a checksum for the update, so it wasn't installed.")
        path, part = os.path.join(folder, name), os.path.join(folder, name + ".part")
        h, n = hashlib.sha256(), 0
        with self._request(asset["url"], "application/octet-stream") as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
                n += len(chunk)
        try:
            if asset.get("size") and n != asset["size"]:
                raise ValueError("The update didn't download completely. Try again later.")
            if want.startswith("sha256:") and want[7:].lower() != h.hexdigest():
                raise ValueError("The downloaded update doesn't match its checksum, so it wasn't installed.")
        except ValueError:
            os.remove(part)
            raise
        os.replace(part, path)
        return path

    def install(self):
        """Download the newer installer and quit; main() starts the installer once the app has let go of the pad."""
        if not FROZEN:
            raise ValueError("This copy runs from Python, so it updates through git, not here. "
                             "Updates are for the installed VSD Pad.")
        st = self.check()
        if not st["available"]:
            return {"ok": True, "message": "You already have the newest version."}
        if self.state["installing"]:
            return {"ok": True, "message": "Already updating…"}
        self._set(installing=True)
        try:
            path = self.download(self.state["asset"])
        except Exception:
            self._set(installing=False)
            raise
        LOG.info("updating to build %s with %s", st["latest"], path)
        self.pending = path
        if self.quit_app:
            threading.Timer(0.5, self.quit_app).start()
        return {"ok": True, "message": f"Updating to build {st['latest']}: VSD Pad restarts in a few seconds."}

    def launch_pending(self):
        """Called by main() after the app has stopped and released its single-instance lock."""
        if not self.pending:
            return False
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # detached, new process group
        subprocess.Popen([self.pending, *INSTALL_ARGS], creationflags=flags, close_fds=True,
                         cwd=os.path.dirname(self.pending))
        LOG.info("started the installer")
        return True

    # ---- background checks --------------------------------------------------------------
    def _idle(self):
        now = time.monotonic()
        return all(now - getattr(r, "last_input", 0) >= IDLE_S for r in self.app.runners())

    def tick(self):
        """One background round: check, and install by itself if allowed and the pad is idle."""
        if not self.configured():
            return
        try:
            st = self.check()
        except ValueError as e:
            LOG.warning("update check: %s", e)
            return
        if st["available"]:
            LOG.info("update available: build %s (this is build %s)", st["latest"], st["current"])
            if FROZEN and self.auto() and self._idle():
                try:
                    self.install()
                except Exception as e:
                    self._set(installing=False, error=str(e))
                    LOG.warning("update failed: %s", e)

    def start(self):
        def loop():
            if self._stop.wait(FIRST_CHECK_S):
                return
            while True:
                self.tick()
                # retry sooner while an update waits for the pad to be idle
                if self._stop.wait(900 if self.state["available"] else EVERY_S):
                    return
        threading.Thread(target=loop, name="updater", daemon=True).start()

    def stop(self):
        self._stop.set()


UPDATER = None   # set by main.py when the app runs
