"""Device contract. A device is any control surface: a grid of LCD keys plus optional plain buttons.

Slots are 1-based, left to right, top to bottom (slot 1 = top-left key). Plain buttons (the M18's round
buttons, the Neo's touch points) have ids above the key count and no screen.
"""
from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    id: str            # stable id used in the config, e.g. "m18" or "sd-CL12K1A01234"
    kind: str          # driver name: "m18", "streamdeck", "fake"
    model: str         # human name, e.g. "Stream Deck MK.2"
    cols: int
    rows: int
    key_px: int
    buttons: list = field(default_factory=list)   # ids of plain (screenless) buttons
    path: bytes = b""  # hid path (driver specific)
    product_id: int = 0

    @property
    def key_count(self):
        return self.cols * self.rows


class Device:
    """Base class for drivers. Methods raise OSError when the device goes away."""

    def __init__(self, info):
        self.info = info

    def open(self): ...
    def close(self): ...
    def set_brightness(self, pct): ...
    def set_key(self, slot, img):
        """img is a square PIL image of any size; the driver scales, rotates and encodes it."""
    def clear(self): ...
    def flush(self):
        """Make the images sent since the last flush visible (no-op on devices that show them at once)."""
    def keepalive(self): ...
    def read_events(self):
        """Return a list of (button_id, pressed). Must not block for long (poll)."""
        return []
