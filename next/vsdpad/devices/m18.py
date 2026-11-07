"""VSDinside VSD M18 (USB 5548:1000, Mirabox/Ajazz-style). Protocol verified on the user's pad:

- 1024-byte output reports (report id 0 + 1024 bytes); commands start with b"CRT\\x00\\x00".
  DIS wake, LIG brightness, CLE clear, STP commit, BAT <len u16 BE> <index> + JPEG chunks, CONNECT keep-alive.
- Keys are 72x72 JPEG. The image index runs bottom-to-top, so rows are flipped (image_index).
- Presses arrive as b"ACK\\x00\\x00OK\\x00\\x00" + id + state; ids 1-15 equal the slots, round buttons are 37, 48, 49.
- Reads must be non-blocking (blocking timeouts caused read-error loops).
"""
import io, time

from PIL import Image

from .base import Device, DeviceInfo

VID, PID = 0x5548, 0x1000
ROUND_IDS = [37, 48, 49]


def info_for(path=b""):
    return DeviceInfo(id="m18", kind="m18", model="VSD M18", cols=5, rows=3, key_px=72,
                      buttons=list(ROUND_IDS), path=path, product_id=PID)


def image_index(slot):
    """Slot (top-to-bottom) -> the pad's image index (bottom-to-top)."""
    if 1 <= slot <= 15:
        row, col = divmod(slot - 1, 5)
        return (2 - row) * 5 + col + 1
    return slot


def encode(img, rotation=0, px=72):
    img = img.convert("RGB")
    if img.size != (px, px):
        img = img.resize((px, px), Image.LANCZOS)
    if rotation:
        img = img.rotate(rotation)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


class M18(Device):
    rotation = 0

    def __init__(self, info, hid_module=None):
        super().__init__(info)
        self._hid = hid_module
        self.d = None

    def open(self):
        hid = self._hid
        if hid is None:
            import hid  # lazily: only needed with real hardware
        self.d = hid.device()
        # Same call as the verified 1.x driver (opens the control interface).
        self.d.open(VID, PID)
        self.d.set_nonblocking(1)
        self.cmd(b"DIS")

    def close(self):
        try:
            if self.d:
                self.d.close()
        except Exception:
            pass
        self.d = None

    def _send(self, b):
        for i in range(0, len(b), 1024):
            n = self.d.write(b"\x00" + b[i:i + 1024].ljust(1024, b"\x00"))
            if n is not None and n < 0:
                raise OSError("write failed")

    def cmd(self, c, *a):
        self._send(b"CRT\x00\x00" + c + bytes(a))

    def set_brightness(self, pct):
        self.cmd(b"LIG\x00\x00", max(0, min(100, int(pct))))

    def clear(self):
        self.cmd(b"CLE\x00\x00\x00", 0xFF)

    def keepalive(self):
        self.cmd(b"CONNECT")

    def set_key(self, slot, img):
        j = encode(img, self.rotation, self.info.key_px)
        self._send(b"CRT\x00\x00BAT\x00\x00" + len(j).to_bytes(2, "big") + bytes([image_index(slot)]))
        self._send(j)

    def flush(self):
        self.cmd(b"STP")

    def read_events(self):
        out = []
        for _ in range(16):  # drain what is queued, never block
            r = self.d.read(1024)
            if not r:
                break
            if bytes(r[:9]) == b"ACK\x00\x00OK\x00\x00" and len(r) > 9:
                out.append((r[9], bool(r[10]) if len(r) > 10 else False))
        if not out:
            time.sleep(0.004)
        return out
