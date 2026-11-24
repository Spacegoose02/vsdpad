# PyInstaller recipe for VSDPad.exe (a folder build: starts fast, fewer antivirus false alarms than one-file).
# Built by installer\build.ps1 or the GitHub "Windows build" workflow; run from the `next` folder.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

NEXT = os.path.abspath(".")
PKG = os.path.join(NEXT, "vsdpad")
# settings page, themes, icon packs and the plugin sources (plugins are found by scanning that folder)
datas = [(os.path.join(PKG, d), os.path.join("vsdpad", d)) for d in ("web", "themes", "iconpacks", "plugins")
         if os.path.isdir(os.path.join(PKG, d))]

# Windows version details (Properties -> Details). Unsigned programs without them look more suspicious to antivirus.
import re, sys
sys.path.insert(0, NEXT)
from vsdpad import __version__
try:
    from vsdpad._build import BUILD
except ImportError:
    BUILD = 0
nums = [int(n) for n in re.findall(r"\d+", __version__)[:3]] + [0, 0, 0]
filevers = (nums[0], nums[1], nums[2], int(BUILD))
pretty = f"{__version__} (build {BUILD})" if BUILD else __version__
strings = {"CompanyName": "Spacegoose02", "FileDescription": "VSD Pad - macro pad controller", "FileVersion": pretty,
           "InternalName": "VSDPad", "LegalCopyright": "Spacegoose02", "OriginalFilename": "VSDPad.exe",
           "ProductName": "VSD Pad", "ProductVersion": pretty}
# PyInstaller's version-file format (what pyi-grab_version writes)
version_text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={filevers}, prodvers={filevers}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [{", ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())}])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
version_file = os.path.join(NEXT, "build", "version_info.txt")
os.makedirs(os.path.dirname(version_file), exist_ok=True)
with open(version_file, "w", encoding="utf-8") as f:
    f.write(version_text)

# Windows "Now playing" and Notifications use the winrt packages (compiled modules in a namespace package): take all of them
w_datas, w_bins, w_hidden = collect_all("winrt")

a = Analysis(
    [os.path.join(NEXT, "run.py")],
    pathex=[NEXT],
    datas=datas + w_datas,
    binaries=w_bins,
    hiddenimports=collect_submodules("vsdpad") + w_hidden + ["pystray._win32", "hid"],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VSDPad", console=False,
          icon=os.path.join(PKG, "web", "vsdpad.ico"), version=version_file, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="VSDPad", upx=False)
