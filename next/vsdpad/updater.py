"""Updates for the installed app (VSDPad.exe).

Every push builds VSDPad-Setup.exe on GitHub and publishes it as a release tagged build-<n> (see
.github/workflows/windows-build.yml). The repo is private, so the app reads releases with a read-only GitHub token
(Plugins -> Updates; stored encrypted like the other tokens). When a newer build is out, the app downloads the installer,
checks its size and SHA-256, quits, and the installer updates it quietly and starts it again (a few seconds).
Your layout lives in %APPDATA%\\VSD Pad and is never touched by an update.
"""
import hashlib, json, os, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

from .log import LOG

REPO = "Spacegoose02/vsdpad"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
FIRST_CHECK_S = 120           # after starting
EVERY_S = 6 * 3600            # then this often
IDLE_S = 600                  # only install by itself when no pad key was pressed for this long
TIMEOUT = 20
FROZEN = getattr(sys, "frozen", False)


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


class Updater:
    def __init__(self, app, quit_app=None, opener=urllib.request.urlopen):
        self.app, self.quit_app, self._open = app, quit_app, opener
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.state = {"current": current_build(), "latest": None, "available": False, "checked": None, "error": None,
                      "installing": False}

    # ---- settings -----------------------------------------------------------------------
    def _settings(self):
        return self.app.store.plugin_settings("updates")

    def token(self):
        return (self._settings().get("token") or "").strip()

    def auto(self):
        return (self._settings().get("auto") or "yes").strip().lower() not in ("no", "off", "false", "0")

    # ---- GitHub -------------------------------------------------------------------------
    def _request(self, url, accept):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "vsdpad-updater",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
        # not passed on when GitHub redirects the download to its file storage (which would refuse it)
        req.add_unredirected_header("Authorization", "Bearer " + self.token())
        return self._open(req, timeout=TIMEOUT)

    def check(self):
        """Look for a newer build. Returns the state dict; raises ValueError with a readable message."""
        if not self.token():
            raise ValueError("Paste your GitHub token in Plugins → Updates first.")
        try:
            with self._request(LATEST, "application/vnd.github+json") as r:
                rel = json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = {401: "GitHub refused the token (it may have expired). Make a new one and paste it in Plugins → Updates.",
                   403: "GitHub refused the token. Check it can read the vsdpad repository (Contents: Read-only).",
                   404: "No update found: either nothing is published yet, or the token can't see the vsdpad repository."
                   }.get(e.code, f"GitHub said {e.code} {e.reason}.")
            self._set(error=msg, checked=time.time())
            raise ValueError(msg) from None
        except (urllib.error.URLError, OSError) as e:
            msg = f"Couldn't reach GitHub ({getattr(e, 'reason', e)})."
            self._set(error=msg, checked=time.time())
            raise ValueError(msg) from None
        latest = _build_of(rel.get("tag_name"))
        asset = next((a for a in rel.get("assets", []) if str(a.get("name", "")).startswith("VSDPad-Setup")
                      and str(a.get("name", "")).endswith(".exe")), None)
        self._set(latest=latest, checked=time.time(), error=None, asset=asset, notes=(rel.get("body") or "")[:2000],
                  available=bool(asset) and latest > self.state["current"])
        return self.public()

    def public(self):
        with self._lock:
            return {k: v for k, v in self.state.items() if k != "asset"}

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def download(self, asset, folder=None):
        folder = folder or tempfile.mkdtemp(prefix="vsdpad-update-")
        path = os.path.join(folder, os.path.basename(asset["name"]))
        h, n = hashlib.sha256(), 0
        with self._request(asset["url"], "application/octet-stream") as r, open(path, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
                n += len(chunk)
        want = str(asset.get("digest") or "")
        if asset.get("size") and n != asset["size"]:
            raise ValueError("The update didn't download completely. Try again later.")
        if want.startswith("sha256:") and want[7:].lower() != h.hexdigest():
            raise ValueError("The downloaded update doesn't match GitHub's checksum, so it wasn't installed.")
        return path

    def install(self):
        """Download the newer installer, start it (after this app has quit) and quit."""
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
        # wait ~7 s so this app has quit (setup checks VSD Pad isn't running), then update quietly; setup restarts it
        flags = 0x08000000 | 0x00000008 | 0x00000200   # no window, detached, new process group
        subprocess.Popen(f'cmd /c ping -n 8 127.0.0.1 >nul & "{path}" /SILENT /SUPPRESSMSGBOXES /NOCANCEL /NORESTART /SP-',
                         creationflags=flags, close_fds=True)
        if self.quit_app:
            threading.Timer(0.5, self.quit_app).start()
        return {"ok": True, "message": f"Updating to build {st['latest']}: VSD Pad restarts in a few seconds."}

    # ---- background checks --------------------------------------------------------------
    def _idle(self):
        now = time.monotonic()
        return all(now - getattr(r, "last_input", 0) >= IDLE_S for r in self.app.runners())

    def tick(self):
        """One background round: check, and install by itself if allowed and the pad is idle."""
        if not self.token():
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
