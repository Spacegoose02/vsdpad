# PyInstaller recipe for VSDPad.exe (a folder build: starts fast, fewer antivirus false alarms than one-file).
# Built by installer\build.ps1 or the GitHub "Windows build" workflow; run from the `next` folder.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

NEXT = os.path.abspath(".")
PKG = os.path.join(NEXT, "vsdpad")
# settings page, themes, icon packs and the plugin sources (plugins are found by scanning that folder)
datas = [(os.path.join(PKG, d), os.path.join("vsdpad", d)) for d in ("web", "themes", "iconpacks", "plugins")
         if os.path.isdir(os.path.join(PKG, d))]

# Windows "Now playing" uses the winrt packages (compiled modules in a namespace package): take all of them
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
          icon=os.path.join(PKG, "web", "vsdpad.ico"), version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="VSDPad", upx=False)
