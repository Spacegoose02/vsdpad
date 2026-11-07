"""Live keys: CPU and memory use of this PC."""
import ctypes, os, sys, threading

from .base import Plugin


def _cpu_times():
    """(idle, total) since boot, in arbitrary units."""
    if sys.platform == "win32":
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            raise OSError("GetSystemTimes failed")
        val = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
        return val(idle), val(kernel) + val(user)  # kernel time includes idle time
    with open("/proc/stat") as f:
        parts = [int(x) for x in f.readline().split()[1:]]
    return parts[3] + (parts[4] if len(parts) > 4 else 0), sum(parts)


def _memory():
    """(used fraction, total GB)."""
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return 1 - m.ullAvailPhys / m.ullTotalPhys, m.ullTotalPhys / 2 ** 30
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    return 1 - info["MemAvailable"] / info["MemTotal"], info["MemTotal"] / 2 ** 20


class SysInfo(Plugin):
    id = "sysinfo"
    name = "PC stats"
    description = "Keys that show CPU and memory use."
    widgets = {
        "cpu": {"label": "CPU use", "interval": 2, "params": [], "sample": {"text": "23%", "label": "CPU", "progress": 0.23}},
        "ram": {"label": "Memory use", "interval": 5, "params": [], "sample": {"text": "61%", "label": "RAM", "progress": 0.61}},
    }

    def __init__(self):
        self._lock = threading.Lock()
        self._last = None

    def widget(self, name, params, ctx):
        if name == "cpu":
            idle, total = _cpu_times()
            with self._lock:
                last, self._last = self._last, (idle, total)
            if not last or total <= last[1]:
                return {"text": "…", "label": "CPU"}
            frac = max(0.0, min(1.0, 1 - (idle - last[0]) / (total - last[1])))
            return {"text": f"{frac * 100:.0f}%", "label": "CPU", "progress": round(frac, 3)}
        if name == "ram":
            frac, _ = _memory()
            return {"text": f"{frac * 100:.0f}%", "label": "RAM", "progress": round(frac, 3)}
        return super().widget(name, params, ctx)


PLUGIN = SysInfo()
