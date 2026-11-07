"""Plugin discovery: every module here exposing PLUGIN is registered. One broken plugin never stops the app."""
import importlib, pkgutil, traceback

from ..log import LOG


def load_plugins():
    found = {}
    for m in pkgutil.iter_modules(__path__):
        if m.name.startswith("_") or m.name == "base":
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{m.name}")
            plugin = getattr(mod, "PLUGIN", None)
            if plugin is not None:
                found[plugin.id] = plugin
        except Exception:
            LOG.error("plugin %s failed to load:\n%s", m.name, traceback.format_exc())
    order = ["system", "pages", "homeassistant", "hue", "wavelength", "clock", "timer", "sysinfo", "weather", "steam"]
    return dict(sorted(found.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else 99, kv[0])))
