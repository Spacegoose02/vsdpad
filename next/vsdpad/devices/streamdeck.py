"""Elgato Stream Deck: the second-generation protocol (Original v2, MK.2, XL, XL v2, Neo, and the keys of the Plus)
and the older first-generation protocol the Stream Deck Mini still uses (see StreamDeckMini below).

Written from the public protocol (as used by python-elgato-streamdeck); talks to the device with the same
`hid` package as the M18 driver, so no extra install is needed. NOT yet tested on real hardware.

- Images: JPEG, sent as 1024-byte output reports: [0x02, 0x07, key, last, len_lo, len_hi, page_lo, page_hi] + up to 1016 bytes.
- Brightness: feature report [0x03, 0x08, percent]; reset: [0x03, 0x02] (both padded to 32 bytes).
- Input report: [0x01, kind, count_lo, count_hi, state0, state1, ...]; kind 0 = keys. Key indexes are 0-based,
  left to right, top to bottom.
- The Original v2 / MK.2 / XL / Neo show images rotated 180 degrees, so we pre-rotate. The Plus does not.
"""
import io, time

from PIL import Image

from .base import Device, DeviceInfo

VID = 0x0FD9
# pid: (model, cols, rows, key_px, flip, extra plain buttons)
MODELS = {
    0x006D: ("Stream Deck", 5, 3, 72, True, 0),
    0x0080: ("Stream Deck MK.2", 5, 3, 72, True, 0),
    0x00A5: ("Stream Deck MK.2", 5, 3, 72, True, 0),
    0x00B9: ("Stream Deck MK.2", 5, 3, 72, True, 0),
    0x006C: ("Stream Deck XL", 8, 4, 96, True, 0),
    0x008F: ("Stream Deck XL", 8, 4, 96, True, 0),
    0x009A: ("Stream Deck Neo", 4, 2, 96, True, 2),
    0x0084: ("Stream Deck +", 4, 2, 120, False, 0),
}
# The Mini (6 keys, 3x2, 80 px) speaks the first-generation protocol. pid: (model, cols, rows, key_px)
MINI_MODELS = {
    0x0063: ("Stream Deck Mini", 3, 2, 80),
    0x0090: ("Stream Deck Mini", 3, 2, 80),   # Mini MK.2
    0x00B3: ("Stream Deck Mini", 3, 2, 80),   # later Mini revision
}
UNSUPPORTED = {0x0060: "Stream Deck (first generation)", 0x0086: "Stream Deck Pedal"}


def info_for(entry):
    """DeviceInfo for one hid.enumerate() entry, or None if not a supported Stream Deck."""
    pid = entry.get("product_id")
    if pid in MINI_MODELS:
        name, cols, rows, px = MINI_MODELS[pid]
        extra = 0
    elif pid in MODELS:
        name, cols, rows, px, _flip, extra = MODELS[pid]
    else:
        return None
    serial = (entry.get("serial_number") or "").strip() or entry.get("path", b"").hex()[-12:]
    keys = cols * rows
    return DeviceInfo(id="sd-" + "".join(c for c in serial if c.isalnum())[:24], kind="streamdeck", model=name,
                      cols=cols, rows=rows, key_px=px, buttons=[keys + 1 + i for i in range(extra)],
                      path=entry.get("path", b""), product_id=entry.get("product_id"))


def encode(img, px, flip):
    img = img.convert("RGB")
    if img.size != (px, px):
        img = img.resize((px, px), Image.LANCZOS)
    if flip:
        img = img.transpose(Image.ROTATE_180)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94)
    return buf.getvalue()


def image_reports(key_index, jpeg, report_len=1024):
    """Split a JPEG into the device's image reports."""
    body = report_len - 8
    out, page, pos = [], 0, 0
    while True:
        chunk = jpeg[pos:pos + body]
        pos += len(chunk)
        last = 1 if pos >= len(jpeg) else 0
        hdr = bytes([0x02, 0x07, key_index, last, len(chunk) & 0xFF, len(chunk) >> 8, page & 0xFF, page >> 8])
        out.append((hdr + chunk).ljust(report_len, b"\x00"))
        page += 1
        if last:
            return out


def parse_states(report, n):
    """Key-down flags from an input report, or None if it is not a key report."""
    if not report or len(report) < 4 or report[0] != 0x01 or report[1] != 0x00:
        return None
    states = list(report[4:4 + n])
    return [bool(s) for s in states] + [False] * (n - len(states))


class StreamDeck(Device):
    def __init__(self, info, hid_module=None):
        super().__init__(info)
        self._hid = hid_module
        self.d = None
        self.flip = MODELS[info.product_id][4]
        self.n = info.key_count + len(info.buttons)
        self._state = [False] * self.n

    def open(self):
        hid = self._hid
        if hid is None:
            import hid
        self.d = hid.device()
        self.d.open_path(self.info.path)
        self.d.set_nonblocking(1)

    def close(self):
        try:
            if self.d:
                self.d.close()
        except Exception:
            pass
        self.d = None

    def _feature(self, *payload):
        self.d.send_feature_report(bytes(payload).ljust(32, b"\x00"))

    def set_brightness(self, pct):
        self._feature(0x03, 0x08, max(0, min(100, int(pct))))

    def clear(self):
        self._feature(0x03, 0x02)

    def set_key(self, slot, img):
        for rep in image_reports(slot - 1, encode(img, self.info.key_px, self.flip)):
            if self.d.write(rep) < 0:
                raise OSError("write failed")

    def read_events(self):
        out = []
        for _ in range(16):
            r = self.d.read(512)
            if not r:
                break
            st = parse_states(r, self.n)
            if st is None:
                continue
            for i, (old, new) in enumerate(zip(self._state, st)):
                if old != new:
                    out.append((i + 1, new))
            self._state = st
        if not out:
            time.sleep(0.004)
        return out


# ---- Stream Deck Mini (first-generation protocol) -------------------------------------------------------------------
# From the public protocol (as used by python-elgato-streamdeck's StreamDeckMini). NOT yet tested on real hardware.
# - Images: 80x80 BMP (24-bit, bottom-up), rotated 90 degrees and flipped top-to-bottom before encoding. Sent as
#   1024-byte output reports: [0x02, 0x01, page, 0x00, last, key + 1, 0 x 10] + up to 1008 bytes.
# - Brightness: feature report [0x05, 0x55, 0xAA, 0xD1, 0x01, percent]; reset: [0x0B, 0x63] (both padded to 17 bytes).
# - Input report: [0x01, state0 .. state5]; keys are 0-based, left to right, top to bottom.
MINI_REPORT_LEN = 1024
MINI_HEADER_LEN = 16


def mini_encode(img, px=80):
    img = img.convert("RGB")
    if img.size != (px, px):
        img = img.resize((px, px), Image.LANCZOS)
    img = img.rotate(90).transpose(Image.FLIP_TOP_BOTTOM)
    buf = io.BytesIO()
    img.save(buf, "BMP")
    return buf.getvalue()


def mini_image_reports(key_index, bmp, report_len=MINI_REPORT_LEN):
    """Split a BMP into the Mini's image reports (pages count from 0, key numbers from 1)."""
    body = report_len - MINI_HEADER_LEN
    out, page, pos = [], 0, 0
    while pos < len(bmp):
        chunk = bmp[pos:pos + body]
        pos += len(chunk)
        last = 1 if pos >= len(bmp) else 0
        hdr = bytes([0x02, 0x01, page & 0xFF, 0x00, last, key_index + 1]).ljust(MINI_HEADER_LEN, b"\x00")
        out.append((hdr + chunk).ljust(report_len, b"\x00"))
        page += 1
    return out


def mini_parse_states(report, n):
    """Key-down flags from a Mini input report, or None if it is not a key report."""
    if not report or len(report) < 2 or report[0] != 0x01:
        return None
    states = list(report[1:1 + n])
    return [bool(s) for s in states] + [False] * (n - len(states))


class StreamDeckMini(StreamDeck):
    def __init__(self, info, hid_module=None):
        Device.__init__(self, info)
        self._hid = hid_module
        self.d = None
        self.n = info.key_count
        self._state = [False] * self.n

    def _feature(self, *payload):
        self.d.send_feature_report(bytes(payload).ljust(17, b"\x00"))

    def set_brightness(self, pct):
        self._feature(0x05, 0x55, 0xAA, 0xD1, 0x01, max(0, min(100, int(pct))))

    def clear(self):
        self._feature(0x0B, 0x63)

    def set_key(self, slot, img):
        for rep in mini_image_reports(slot - 1, mini_encode(img, self.info.key_px)):
            if self.d.write(rep) < 0:
                raise OSError("write failed")

    def read_events(self):
        out = []
        for _ in range(16):
            r = self.d.read(64)
            if not r:
                break
            st = mini_parse_states(r, self.n)
            if st is None:
                continue
            for i, (old, new) in enumerate(zip(self._state, st)):
                if old != new:
                    out.append((i + 1, new))
            self._state = st
        if not out:
            time.sleep(0.004)
        return out
