"""The VSD Pad server's update mirror (no network: a pretend GitHub).  Run:  python tests/test_update_mirror.py"""
import hashlib, io, json, os, shutil, sys, tempfile, unittest, urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad import updater  # noqa: E402
from vsdpad.update_mirror import UpdateMirror, MirrorError  # noqa: E402

BLOB = b"MZ pretend installer" * 100
NAME = "VSDPad-Setup-2.0.0-dev-build9.exe"


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeGitHub:
    def __init__(self, digest=None, status=200):
        self.calls, self.status = [], status
        self.digest = digest if digest is not None else "sha256:" + hashlib.sha256(BLOB).hexdigest()

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "nope", {}, None)
        if req.full_url == updater.LATEST:
            return FakeResp(json.dumps({"tag_name": "build-9", "name": "VSD Pad build 9", "body": "notes", "assets": [
                {"name": NAME, "url": "https://api.github.com/assets/9", "size": len(BLOB), "digest": self.digest},
                {"name": "something-else.zip", "url": "https://api.github.com/assets/10", "size": 1}]}).encode())
        return FakeResp(BLOB)


class TestMirror(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_latest_points_pcs_at_the_server(self):
        gh = FakeGitHub()
        m = UpdateMirror(self.d, "github_pat_x", gh)
        rel = m.latest()
        self.assertEqual(rel["tag_name"], "build-9")
        self.assertEqual([a["name"] for a in rel["assets"]], [NAME])
        self.assertEqual(rel["assets"][0]["url"], "/sync/updates/asset/" + NAME)
        self.assertNotIn("api.github.com", json.dumps(rel))
        self.assertEqual(gh.calls[0].unredirected_hdrs.get("Authorization"), "Bearer github_pat_x")
        m.latest()
        self.assertEqual(len(gh.calls), 1)   # cached

    def test_asset_downloaded_once_and_checked(self):
        gh = FakeGitHub()
        m = UpdateMirror(self.d, lambda: "t", gh)
        p = m.asset_path(NAME)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), BLOB)
        n = len(gh.calls)
        self.assertEqual(m.asset_path(NAME), p)
        self.assertEqual(len(gh.calls), n)   # served from the cache
        for bad in ("../../etc/passwd", "evil.exe", "VSDPad-Setup-x.exe"):
            with self.assertRaises(MirrorError):
                m.asset_path(bad)

    def test_bad_checksum_is_not_served(self):
        m = UpdateMirror(self.d, "t", FakeGitHub(digest="sha256:" + "0" * 64))
        with self.assertRaises(MirrorError) as e:
            m.asset_path(NAME)
        self.assertEqual(e.exception.status, 502)
        self.assertEqual([f for f in os.listdir(os.path.join(self.d, "updates"))], [])

    def test_no_token_or_github_down(self):
        os.environ.pop("VSDPAD_GITHUB_TOKEN", None)
        with self.assertRaises(MirrorError) as e:
            UpdateMirror(self.d, None, FakeGitHub()).latest()
        self.assertEqual(e.exception.status, 404)
        with self.assertRaises(MirrorError) as e:
            UpdateMirror(self.d, "t", FakeGitHub(status=500)).latest()
        self.assertEqual(e.exception.status, 502)


if __name__ == "__main__":
    unittest.main(verbosity=1)
