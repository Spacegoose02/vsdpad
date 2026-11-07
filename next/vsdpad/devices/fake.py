"""An in-memory device for tests and for running without hardware (--fake). Records what is drawn."""
import threading

from .base import Device, DeviceInfo


def info_for(cols=5, rows=3, buttons=(37, 48, 49), id="fake", model="Simulated pad"):
    return DeviceInfo(id=id, kind="fake", model=model, cols=cols, rows=rows, key_px=72, buttons=list(buttons))


class FakeDevice(Device):
    def __init__(self, info):
        super().__init__(info)
        self.images, self.brightness, self.flushes, self.sent = {}, None, 0, 0
        self.log = []  # slots in the order they were drawn
        self._events, self._lock = [], threading.Lock()
        self.fail_next = None  # set to an exception to simulate an unplug
        self.opened = False

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def _check(self):
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e

    def set_brightness(self, pct):
        self._check()
        self.brightness = pct

    def clear(self):
        self.images.clear()

    def set_key(self, slot, img):
        self._check()
        self.images[slot] = img.copy()
        self.sent += 1
        self.log.append(slot)

    def flush(self):
        self.flushes += 1

    def press(self, button, pressed=True):
        with self._lock:
            self._events.append((button, pressed))

    def read_events(self):
        self._check()
        with self._lock:
            ev, self._events = self._events, []
        if not ev:
            import time
            time.sleep(0.004)
        return ev
