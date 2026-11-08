"""Start with Windows + installed-app first run (no Windows needed).  Run:  python tests/test_autostart.py"""
import json, os, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vsdpad import autostart, main  # noqa: E402


def lnk(path, target):
    """A stand-in .lnk: real ones store the target path as UTF-16 text, which is all autostart looks at."""
    with open(path, "wb") as f:
        f.write(b"L\0\0\0" + target.encode("utf-16-le"))


class TestAutostart(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="vsdpad-auto-")
        self.startup = os.path.join(self.d, "Startup")
        self.data = os.path.join(self.d, "data")
        os.makedirs(self.startup)
        self.made = []

        def fake_ps(script, env):  # stands in for PowerShell creating the shortcut
            self.made.append(env)
            lnk(env["VSDPAD_LNK"], env["VSDPAD_TARGET"] + " " + env["VSDPAD_ARGS"])
        self.patches = [mock.patch.object(autostart, "supported", lambda: True),
                        mock.patch.object(autostart, "startup_dir", lambda: self.startup),
                        mock.patch.object(autostart, "_powershell", fake_ps)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_source_command_runs_run_py_without_console_or_browser(self):
        target, args, cwd, _ = autostart.command(self.data, self.data)
        self.assertTrue(args.endswith('run.py') or args.endswith('run.py"'))
        self.assertNotIn("--gui", args)
        self.assertNotIn("--data", args)          # default data folder: nothing extra
        self.assertEqual(cwd, os.path.dirname(os.path.dirname(autostart.PKG_DIR)) and cwd)
        _, args2, _, _ = autostart.command(self.data, os.path.join(self.d, "other"))
        self.assertIn("--data", args2)

    def test_frozen_command_runs_the_exe(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Users\x\AppData\Local\Programs\VSD Pad\VSDPad.exe"):
            target, args, _, icon = autostart.command()
        self.assertTrue(target.endswith("VSDPad.exe"))
        self.assertEqual(args, "")
        self.assertEqual(icon, target)

    def test_on_off(self):
        st = autostart.status(self.data, self.data)
        self.assertEqual((st["supported"], st["on"], st["others"]), (True, False, []))
        self.assertEqual(autostart.enable(self.data, self.data), [])
        st = autostart.status(self.data, self.data)
        self.assertTrue(st["on"])
        self.assertTrue(st["this_copy"])
        self.assertNotIn("--gui", self.made[0]["VSDPAD_ARGS"])
        autostart.enable(self.data, self.data)    # again: just refreshes it
        self.assertEqual(os.listdir(self.startup), [autostart.NAME])
        autostart.disable()
        self.assertFalse(autostart.status(self.data, self.data)["on"])
        autostart.disable()                       # already off: fine

    def test_other_vsdpad_shortcuts_are_moved_aside_not_deleted(self):
        lnk(os.path.join(self.startup, "VSD Pad 2.lnk"), r"C:\code\vsdpad\next\run.py")
        lnk(os.path.join(self.startup, "VSDPad-old.lnk"), r"C:\Program Files\Python310\pythonw.exe C:\CODE\VSDPAD\app.py")
        lnk(os.path.join(self.startup, "VSD Craft.lnk"), r"C:\Program Files\VSD Craft\VSD Craft.exe")   # not ours
        lnk(os.path.join(self.startup, "Discord.lnk"), r"C:\Discord\Update.exe")
        self.assertEqual(autostart.status(self.data, self.data)["others"], ["VSD Pad 2.lnk", "VSDPad-old.lnk"])
        moved = autostart.enable(self.data, self.data)
        self.assertEqual(moved, ["VSD Pad 2.lnk", "VSDPad-old.lnk"])
        self.assertEqual(sorted(os.listdir(self.startup)), ["Discord.lnk", "VSD Craft.lnk", autostart.NAME])
        self.assertEqual(sorted(os.listdir(os.path.join(self.data, autostart.BACKUP))), ["VSD Pad 2.lnk", "VSDPad-old.lnk"])
        lnk(os.path.join(self.startup, "VSD Pad 2.lnk"), r"C:\code\vsdpad\next\run.py")   # same name again: kept both
        autostart.enable(self.data, self.data)
        self.assertIn("VSD Pad 2 (2).lnk", os.listdir(os.path.join(self.data, autostart.BACKUP)))

    def test_shortcut_from_another_copy(self):
        lnk(os.path.join(self.startup, autostart.NAME), r"C:\Users\x\AppData\Local\Programs\VSD Pad\VSDPad.exe")
        st = autostart.status(self.data, self.data)
        self.assertTrue(st["on"])
        self.assertFalse(st["this_copy"])

    def test_not_windows(self):
        with mock.patch.object(autostart, "supported", lambda: False):
            self.assertFalse(autostart.status()["supported"])
            with self.assertRaises(ValueError):
                autostart.enable(self.data)


class TestInstalledFirstRun(unittest.TestCase):
    def test_copies_previous_data_but_not_the_log(self):
        d = tempfile.mkdtemp(prefix="vsdpad-prev-")
        try:
            prev, new = os.path.join(d, "prev"), os.path.join(d, "new")
            os.makedirs(os.path.join(prev, "images"))
            with open(os.path.join(prev, "config.json"), "w") as f:
                json.dump({"version": 5, "marker": "mine"}, f)
            open(os.path.join(prev, "images", "a.png"), "wb").close()
            open(os.path.join(prev, "vsdpad.log"), "w").close()
            with mock.patch.object(main, "PREVIOUS_DATA", prev):
                self.assertTrue(main.first_run(new, log=lambda *a: None))
                self.assertFalse(main.first_run(new, log=lambda *a: None))   # only the first time
            with open(os.path.join(new, "config.json")) as f:
                self.assertEqual(json.load(f)["marker"], "mine")
            self.assertTrue(os.path.exists(os.path.join(new, "images", "a.png")))
            self.assertFalse(os.path.exists(os.path.join(new, "vsdpad.log")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_icon(self):
        self.assertEqual(main.app_icon(256).size, (256, 256))


if __name__ == "__main__":
    unittest.main(verbosity=1)
