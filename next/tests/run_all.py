"""Run every check:  python tests\\run_all.py   (no pad needed)"""
import compileall, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = compileall.compile_dir(os.path.join(ROOT, "vsdpad"), quiet=1)
print("compile", "ok" if ok else "FAILED")
codes = [0 if ok else 1]
for t in ("test_core.py", "test_plugins.py", "test_server.py", "test_integrations.py", "test_feeds.py", "test_iconpacks.py", "test_visual.py", "test_autostart.py", "test_updater.py", "test_raccoon.py"):
    print(f"\n== {t} ==", flush=True)
    codes.append(subprocess.call([sys.executable, os.path.join(ROOT, "tests", t)], cwd=ROOT))
print("\nALL PASSED" if not any(codes) else "\nSOME CHECKS FAILED")
sys.exit(1 if any(codes) else 0)
