"""Pad hardware driver and icon rendering for the VSD M18 (USB 5548:1000).

Protocol notes (all verified on the device, see HANDOFF.md section 3):
- 1024-byte output reports, commands start with b"CRT\\x00\\x00".
- LCD keys are 72x72 JPEG, rotation 0. Reads must be non-blocking.
- Press IDs run bottom-to-top; press_to_slot() converts them to on-screen slots.
"""
import io, time
from PIL import Image, ImageDraw, ImageFont

VID, PID = 0x5548, 0x1000
KEY_SIZE = 72


class Pad:
    rotation = 0  # degrees counter-clockwise; set from config "rotation"

    def __init__(self):
        import hid  # imported lazily so tools that never touch the pad don't need it
        self.d = hid.device()
        self.d.open(VID, PID)
        self.d.set_nonblocking(1)

    def _send(self, b):
        for i in range(0, len(b), 1024):
            self.d.write(b"\x00" + b[i:i + 1024].ljust(1024, b"\x00"))

    def cmd(self, c, *a):
        self._send(b"CRT\x00\x00" + c + bytes(a))

    def wake(self, brightness):
        self.cmd(b"DIS")
        self.cmd(b"LIG\x00\x00", brightness)

    def clear(self):
        self.cmd(b"CLE\x00\x00\x00", 0xFF)

    def keepalive(self):
        self.cmd(b"CONNECT")

    def set_image(self, slot, img):
        """Draw on the key at on-screen slot 1-15 (1-5 = top row on the device)."""
        img = img.convert("RGB").resize((KEY_SIZE, KEY_SIZE), Image.LANCZOS).rotate(self.rotation)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        j = buf.getvalue()
        self._send(b"CRT\x00\x00BAT\x00\x00" + len(j).to_bytes(2, "big") + bytes([image_index(slot)]))
        self._send(j)

    def refresh(self):
        self.cmd(b"STP")

    def read_event(self):
        """Return (press_id, state) or None. state: 1 pressed, 0 released."""
        r = self.d.read(1024)  # non-blocking (see __init__)
        if not r:
            time.sleep(0.005)
            return None
        if bytes(r[:9]) == b"ACK\x00\x00OK\x00\x00":
            return r[9], (r[10] if len(r) > 10 else 0)
        return None

    def close(self):
        try:
            self.d.close()
        except Exception:
            pass


def image_index(slot):
    """Config/GUI slots run top-to-bottom (1-5 = top row, 6-10 = middle, 11-15 = bottom, left to
    right). The pad's *image* index for BAT runs bottom-to-top, so rows are flipped. Verified on the
    device: drawing slot 1 with index 1 put it on the bottom row (user report), which also flips wallpapers."""
    if 1 <= slot <= 15:
        row, col = divmod(slot - 1, 5)
        return (2 - row) * 5 + col + 1
    return slot


def press_to_slot(key):
    """Press IDs match the on-screen slots directly: physical top-left key = ID 1 ... bottom-right = 15
    (derived from the image-index flip above plus the earlier observation that the keys drawn with
    image indexes 1-5 report IDs 11-15). Round buttons keep their raw IDs (0x25, 0x30, 0x31)."""
    return key


def font(size):
    for f in ("seguisym.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render(spec, dy=0):
    """Dark rounded tile, big glyph, small label (the built-in 'modern' look).
    Returns a 170x170 RGB image; Pad.set_image scales it to the 72x72 key.
    dy shifts the whole picture down (170-px units): outline, glyph and label together."""
    s = 170
    img = Image.new("RGB", (s, s), (18, 18, 22))
    d = ImageDraw.Draw(img)
    color = tuple(spec.get("color", (90, 140, 255)))
    d.rounded_rectangle((9, 9 + dy, s - 10, s - 10 + dy), 22, fill=(34, 36, 44), outline=color, width=8)
    maxw = s - 44  # keep text inside a safe margin
    if spec.get("icon"):
        size = 64
        while size > 20 and d.textlength(spec["icon"], font=font(size)) > maxw:
            size -= 4
        d.text((s / 2, 70 + dy), spec["icon"], fill=color, font=font(size), anchor="mm")
    if spec.get("label"):
        size = 28
        while size > 12 and d.textlength(spec["label"], font=font(size)) > maxw:
            size -= 2
        d.text((s / 2, (128 if spec.get("icon") else 85) + dy), spec["label"],
               fill=(235, 235, 240), font=font(size), anchor="mm")
    return img
