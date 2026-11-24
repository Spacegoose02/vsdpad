"""Update mirror for the VSD Pad server (Unraid/Docker): hands GitHub's newest Windows build to the linked PCs.

The repository is private, so only the server holds a GitHub token (the VSDPAD_GITHUB_TOKEN environment variable, or
the token in the server's Plugins -> Updates). PCs ask the server instead of GitHub:

  GET /sync/updates/latest          -> GitHub's releases/latest JSON, with each installer's "url" pointing back here
  GET /sync/updates/asset/<name>    -> the installer, downloaded once from GitHub, checked, and kept in <data>/updates

Both are behind the same password as the config sync. Nothing here runs on a normal PC.
"""
import hashlib, json, os, re, threading, time, urllib.error, urllib.parse, urllib.request

from .log import LOG
from .updater import LATEST, TIMEOUT

CACHE_S = 600                 # ask GitHub at most this often
NAME = re.compile(r"^VSDPad-Setup[\w.\-]*\.exe$")
ASSET_PATH = "/sync/updates/asset/"


class MirrorError(Exception):
    """status: the HTTP status to answer the PC with."""
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class UpdateMirror:
    def __init__(self, data_dir, token=None, opener=urllib.request.urlopen):
        self.folder = os.path.join(data_dir, "updates")
        self._token, self._open = token, opener
        self._lock = threading.Lock()
        self._release, self._fetched = None, 0.0

    def token(self):
        t = self._token() if callable(self._token) else self._token
        return (os.environ.get("VSDPAD_GITHUB_TOKEN") or t or "").strip()

    def _request(self, url, accept):
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "vsdpad-update-mirror",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
        if self.token():
            req.add_unredirected_header("Authorization", "Bearer " + self.token())
        return self._open(req, timeout=TIMEOUT)

    def _github_release(self):
        with self._lock:
            if self._release is not None and time.time() - self._fetched < CACHE_S:
                return self._release
        if not self.token():
            raise MirrorError(404, "The server has no GitHub token (set VSDPAD_GITHUB_TOKEN).")
        try:
            with self._request(LATEST, "application/vnd.github+json") as r:
                rel = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise MirrorError(502, f"GitHub said {e.code} {e.reason}") from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise MirrorError(502, f"couldn't reach GitHub ({getattr(e, 'reason', e)})") from None
        with self._lock:
            self._release, self._fetched = rel, time.time()
        return rel

    def latest(self):
        """The newest release for a PC: same shape as GitHub's, installers only, urls pointing at this server."""
        rel = self._github_release()
        assets = [{"name": a["name"], "size": a.get("size"), "digest": a.get("digest"),
                   "url": ASSET_PATH + urllib.parse.quote(a["name"])}
                  for a in rel.get("assets", []) if NAME.match(str(a.get("name", "")))
                  and str(a.get("digest") or "").startswith("sha256:")]
        return {"tag_name": rel.get("tag_name"), "name": rel.get("name"), "body": rel.get("body") or "",
                "published_at": rel.get("published_at"), "assets": assets}

    def asset_path(self, name):
        """Local copy of the newest release's installer `name` (downloaded and checked on first request)."""
        name = urllib.parse.unquote(name)
        if not NAME.match(name):
            raise MirrorError(404, "not an installer")
        rel = self._github_release()
        asset = next((a for a in rel.get("assets", []) if a.get("name") == name), None)
        if asset is None:
            raise MirrorError(404, "no such installer in the newest release")
        want = str(asset.get("digest") or "")
        if not want.startswith("sha256:"):
            raise MirrorError(404, "GitHub gave no checksum for this installer")
        path = os.path.join(self.folder, name)
        with self._lock:   # one download at a time; other PCs wait for it and then share the file
            if os.path.exists(path) and _sha256(path) == want[7:].lower():
                return path
            os.makedirs(self.folder, exist_ok=True)
            for old in os.listdir(self.folder):   # keep only the newest
                if old != name:
                    try:
                        os.remove(os.path.join(self.folder, old))
                    except OSError:
                        pass
            part, h, n = path + ".part", hashlib.sha256(), 0
            try:
                with self._request(asset["url"], "application/octet-stream") as r, open(part, "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        h.update(chunk)
                        f.write(chunk)
                        n += len(chunk)
            except (urllib.error.URLError, OSError) as e:
                raise MirrorError(502, f"couldn't download the installer from GitHub ({getattr(e, 'reason', e)})") from None
            if (asset.get("size") and n != asset["size"]) or h.hexdigest() != want[7:].lower():
                os.remove(part)
                raise MirrorError(502, "the installer from GitHub didn't match its checksum")
            os.replace(part, path)
            LOG.info("update mirror: cached %s", name)
            return path


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
