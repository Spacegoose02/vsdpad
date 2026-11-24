"""Device discovery: finds every supported pad on USB."""
from . import m18, streamdeck
from .base import Device, DeviceInfo  # noqa: F401


def discover(hid_module=None):
    """Return (infos, notes). notes are human-readable messages about devices we can't drive."""
    hid = hid_module
    if hid is None:
        try:
            import hid
        except ImportError:
            return [], ["The 'hidapi' package is not installed (pip install hidapi)."]
    infos, notes, seen = [], [], set()
    try:
        entries = hid.enumerate()
    except Exception as e:  # pragma: no cover - driver trouble
        return [], [f"USB scan failed: {e}"]
    for e in entries:
        vid, pid = e.get("vendor_id"), e.get("product_id")
        if vid == m18.VID and pid == m18.PID:
            if "m18" not in seen:
                seen.add("m18")
                infos.append(m18.info_for(e.get("path", b"")))
        elif vid == streamdeck.VID:
            info = streamdeck.info_for(e)
            if info and info.id not in seen:
                seen.add(info.id)
                infos.append(info)
            elif not info and pid in streamdeck.UNSUPPORTED:
                notes.append(f"{streamdeck.UNSUPPORTED[pid]} found but not supported yet.")
    return infos, sorted(set(notes))


def open_device(info, hid_module=None):
    if info.kind == "m18":
        return m18.M18(info, hid_module)
    if info.kind == "streamdeck":
        if info.product_id in streamdeck.MINI_MODELS:
            return streamdeck.StreamDeckMini(info, hid_module)
        return streamdeck.StreamDeck(info, hid_module)
    raise ValueError(f"no driver for {info.kind}")
