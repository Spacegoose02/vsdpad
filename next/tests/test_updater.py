"""Updates from GitHub releases (no network: a pretend GitHub).  Run:  python tests/test_updater.py"""
import hashlib, io, json, os, shutil, sys, tempfile, unittest, urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad import updater  # noqa: E402
from vsdpad.plugins import updates as updates_plugin  # noqa: E402

BLOB = b"MZ pretend installer" * 100


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeGitHub:
    def __init__(self, tag="build-7", status=200, digest=None, size=None):
        self.tag, self.status, self.calls = tag, status, []
        self.digest = digest if digest is not None else "sha256:" + hashlib.sha256(BLOB).hexdigest()
        self.size = size if size is not None else len(BLOB)

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "nope", {}, None)
        if req.full_url == updater.LATEST:
            return FakeResp(json.dumps({"tag_name": self.tag, "body": "What's new", "assets": [
                {"name": "VSDPad-Setup-2.0.0-dev-build7.exe", "url": "https://api.github.com/asset/1",
                 "size": self.size, "digest": self.digest}]}).encode())
        return FakeResp(BLOB)


class FakeStore:
    def __init__(self, s):
        self.s = s

    def plugin_settings(self, pid):
        return dict(self.s) if pid == "updates" else {}


class FakeApp:
    def __init__(self, settings, idle_for=10_000):
        self.store = FakeStore(settings)
        self.idle_for = idle_for

    def runners(self):
        import time
        return [mock.Mock(last_input=time.monotonic() - self.idle_for)]


def make(settings=None, gh=None, idle_for=10_000, build=3):
    gh = gh or FakeGitHub()
    quit_app = mock.Mock()
    with mock.patch.object(updater, "current_build", lambda: build):
        u = updater.Updater(FakeApp({"token": "github_pat_x"} if settings is None else settings, idle_for), quit_app, gh)
    return u, gh, quit_app


class TestUpdater(unittest.TestCase):
    def test_needs_a_token(self):
        u, gh, _ = make(settings={})
        with self.assertRaisesRegex(ValueError, "token"):
            u.check()
        self.assertEqual(gh.calls, [])
        u.tick()   # background: quietly does nothing

    def test_newer_build_available_and_token_sent(self):
        u, gh, _ = make()
        st = u.check()
        self.assertTrue(st["available"])
        self.assertEqual((st["current"], st["latest"]), (3, 7))
        self.assertNotIn("asset", st)
        self.assertEqual(gh.calls[0].unredirected_hdrs.get("Authorization"), "Bearer github_pat_x")
        self.assertNotIn("Authorization", gh.calls[0].headers)   # not forwarded to the download storage

    def test_up_to_date(self):
        u, _, _ = make(gh=FakeGitHub(tag="build-3"))
        self.assertFalse(u.check()["available"])

    def test_readable_errors(self):
        for code, words in ((401, "expired"), (403, "Read-only"), (404, "can't see")):
            u, _, _ = make(gh=FakeGitHub(status=code))
            with self.assertRaisesRegex(ValueError, words):
                u.check()
            self.assertIn(words, u.public()["error"])

    def test_download_checks_size_and_checksum(self):
        d = tempfile.mkdtemp()
        try:
            u, _, _ = make()
            u.check()
            path = u.download(u.state["asset"], d)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), BLOB)
            u, _, _ = make(gh=FakeGitHub(digest="sha256:" + "0" * 64))
            u.check()
            with self.assertRaisesRegex(ValueError, "checksum"):
                u.download(u.state["asset"], d)
            u, _, _ = make(gh=FakeGitHub(size=5))
            u.check()
            with self.assertRaisesRegex(ValueError, "completely"):
                u.download(u.state["asset"], d)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_python_version_never_installs(self):
        u, _, quit_app = make()
        with mock.patch.object(updater, "FROZEN", False), mock.patch.object(updater.subprocess, "Popen") as pop:
            with self.assertRaisesRegex(ValueError, "git"):
                u.install()
            u.tick()
        pop.assert_not_called()
        quit_app.assert_not_called()

    def test_installed_app_installs_quietly_and_quits(self):
        u, _, quit_app = make()
        with mock.patch.object(updater, "FROZEN", True), mock.patch.object(updater.subprocess, "Popen") as pop, \
                mock.patch.object(updater.threading, "Timer") as timer:
            r = u.install()
        self.assertIn("build 7", r["message"])
        cmd = pop.call_args[0][0]
        self.assertIn("/SILENT", cmd)
        self.assertIn("VSDPad-Setup", cmd)
        timer.assert_called_once()
        self.assertIs(timer.call_args[0][1], quit_app)

    def test_background_waits_for_idle_pad_and_respects_no(self):
        for settings, idle, expect in (({"token": "t"}, 10_000, True), ({"token": "t"}, 5, False),
                                       ({"token": "t", "auto": "no"}, 10_000, False)):
            u, _, _ = make(settings=settings, idle_for=idle)
            with mock.patch.object(updater, "FROZEN", True), mock.patch.object(u, "install") as inst:
                u.tick()
            self.assertEqual(inst.called, expect, (settings, idle))

    def test_plugin(self):
        u, _, _ = make()
        with mock.patch.object(updater, "UPDATER", u):
            p = updates_plugin.PLUGIN
            self.assertEqual(p.secret_keys(), {"token"})
            self.assertEqual(p.widget("status", {}, None)["label"], "Updates")
            r = p.api("check", {}, None)
            self.assertTrue(r["ok"])
            self.assertIn("Build 7 is out", r["message"])
            self.assertEqual(p.widget("status", {}, None)["text"], "⬇")
        with mock.patch.object(updater, "UPDATER", make(gh=FakeGitHub(status=401))[0]):
            self.assertFalse(updates_plugin.PLUGIN.api("check", {}, None)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
