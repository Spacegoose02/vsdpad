"""Run every check that needs no pad: compile all Python files, then both test suites.
Usage:  python tests/run_all.py     (exit code 0 = everything passed)
"""
import os, py_compile, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True

print("== compile ==")
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", "icons", ".git")]
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(base, f)
            try:
                py_compile.compile(path, doraise=True)
            except py_compile.PyCompileError as e:
                ok = False
                print("FAIL", path, e)
print("compile ok" if ok else "compile FAILED")

for name in ("test_pages.py", "test_plugins.py"):
    print(f"\n== {name} ==")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", name)], cwd=ROOT)
    ok = ok and r.returncode == 0

print("\nALL PASSED" if ok else "\nSOMETHING FAILED")
sys.exit(0 if ok else 1)
