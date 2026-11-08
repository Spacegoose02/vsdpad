"""Start with Windows: a "VSD Pad" shortcut in the user's Startup folder.

The shortcut starts VSD Pad quietly (no console, no browser). Turning it on also moves any other shortcut in the
Startup folder that starts VSD Pad (e.g. a hand-made one, or the 1.x app's) into <data>/startup-backup, so only
one copy starts. Nothing is deleted: moving a shortcut back to the Startup folder undoes that.
"""
import os, shutil, subprocess, sys

from . import PKG_DIR

NAME = "VSD Pad.lnk"
BACKUP = "startup-backup"
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: don't flash a PowerShell window


def supported():
    return sys.platform == "win32"


def startup_dir():
    appdata = os.environ.get("APPDATA")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup") if appdata else None


def command(data_dir=None, default_data=None):
    """(target, arguments, working folder, icon) for the shortcut: this same copy of VSD Pad, without a console."""
    if getattr(sys, "frozen", False):       # the installed VSDPad.exe
        target, args = sys.executable, []
        cwd, icon = os.path.dirname(sys.executable), sys.executable
    else:                                   # running from source with Python
        target = sys.executable
        w = os.path.join(os.path.dirname(target), "pythonw.exe")
        if os.path.basename(target).lower() == "python.exe" and os.path.exists(w):
            target = w
        run = os.path.join(os.path.dirname(PKG_DIR), "run.py")
        args, cwd = [run], os.path.dirname(run)
        ico = os.path.join(PKG_DIR, "web", "vsdpad.ico")
        icon = ico if os.path.exists(ico) else ""
    if data_dir and default_data and os.path.normcase(os.path.abspath(data_dir)) != os.path.normcase(os.path.abspath(default_data)):
        args += ["--data", os.path.abspath(data_dir)]
    return target, subprocess.list2cmdline(args), cwd, icon


def _mentions(path, *words):
    """Does this .lnk file mention any of the words (paths are stored as UTF-16 or ANSI text)?"""
    try:
        with open(path, "rb") as f:
            raw = f.read(64_000).lower()
    except OSError:
        return False
    return any(w.lower().encode("utf-16-le") in raw or w.lower().encode("mbcs" if sys.platform == "win32" else "latin-1", "ignore") in raw
               for w in words if w)


def _others(folder):
    """Other shortcuts in the Startup folder that look like they start VSD Pad (old or new)."""
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for n in names:
        # "vsdpad" is in every path of this app (C:\code\vsdpad, VSDPad.exe), but not in VSD Craft's
        if n.lower().endswith(".lnk") and n != NAME and _mentions(os.path.join(folder, n), "vsdpad"):
            out.append(n)
    return out


def status(data_dir=None, default_data=None):
    if not supported():
        return {"supported": False, "on": False, "others": []}
    folder = startup_dir()
    path = os.path.join(folder, NAME) if folder else None
    on = bool(path and os.path.exists(path))
    target, args, _, _ = command(data_dir, default_data)
    # a shortcut made by another copy (e.g. the Python version vs the installed one) starts that copy instead
    here = args.split(" ")[0].strip('"') if args else target
    return {"supported": True, "on": on, "folder": folder, "others": _others(folder) if folder else [],
            "this_copy": (not on) or _mentions(path, here),
            "backup": os.path.join(os.path.abspath(data_dir), BACKUP) if data_dir else BACKUP}


def _powershell(script, env):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       env={**os.environ, **env}, capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "PowerShell failed").strip().splitlines()[-1])


def enable(data_dir, default_data=None):
    """Create (or refresh) the Startup shortcut; move other VSD Pad startup shortcuts aside. Returns what was moved."""
    if not supported():
        raise ValueError("Start with Windows only works on Windows.")
    folder = startup_dir()
    if not folder:
        raise OSError("Can't find your Startup folder (APPDATA isn't set).")
    os.makedirs(folder, exist_ok=True)
    target, args, cwd, icon = command(data_dir, default_data)
    # values go through environment variables, so no quoting problems with spaces or quotes in paths
    _powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VSDPAD_LNK); "
                "$s.TargetPath = $env:VSDPAD_TARGET; $s.Arguments = $env:VSDPAD_ARGS; "
                "$s.WorkingDirectory = $env:VSDPAD_CWD; $s.Description = 'VSD Pad (starts with Windows)'; "
                "if ($env:VSDPAD_ICON) { $s.IconLocation = $env:VSDPAD_ICON }; $s.Save()",
                {"VSDPAD_LNK": os.path.join(folder, NAME), "VSDPAD_TARGET": target, "VSDPAD_ARGS": args,
                 "VSDPAD_CWD": cwd, "VSDPAD_ICON": icon})
    moved = []
    for n in _others(folder):
        dest_dir = os.path.join(data_dir, BACKUP)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, n)
        if os.path.exists(dest):
            base, ext = os.path.splitext(n)
            i = 2
            while os.path.exists(os.path.join(dest_dir, f"{base} ({i}){ext}")):
                i += 1
            dest = os.path.join(dest_dir, f"{base} ({i}){ext}")
        try:
            shutil.move(os.path.join(folder, n), dest)
            moved.append(n)
        except OSError:
            pass
    return moved


def disable():
    if not supported():
        return
    folder = startup_dir()
    path = os.path.join(folder, NAME) if folder else None
    if path and os.path.exists(path):
        os.remove(path)
