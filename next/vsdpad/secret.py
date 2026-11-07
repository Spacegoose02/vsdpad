"""Protect plugin secrets (Home Assistant token, Hue key) at rest.

On Windows the value is encrypted with DPAPI for the current Windows user ("dpapi:<base64>"), so a copied
config file is useless on another account or PC. Elsewhere (tests, other OSes) values stay as plain text.
"""
import base64, ctypes, sys

PREFIX = "dpapi:"


def available():
    return sys.platform == "win32"


if sys.platform == "win32":  # pragma: no cover - exercised on the user's PC
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data):
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data):
        src, _keep = _blob(data)
        out = _Blob()
        if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):  # CRYPTPROTECT_UI_FORBIDDEN
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def _protect(data):
        return _call(ctypes.windll.crypt32.CryptProtectData, data)

    def _unprotect(data):
        return _call(ctypes.windll.crypt32.CryptUnprotectData, data)


def protect(value):
    if not value or not available() or value.startswith(PREFIX):
        return value
    return PREFIX + base64.b64encode(_protect(value.encode("utf-8"))).decode()


def unprotect(value):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    if not available():
        raise ValueError("this secret was encrypted on Windows and can't be read here")
    try:
        return _unprotect(base64.b64decode(value[len(PREFIX):])).decode("utf-8")
    except Exception:
        return ""  # encrypted by another Windows user / PC: treat as not set, the user re-enters it
