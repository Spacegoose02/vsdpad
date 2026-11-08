"""A small WebSocket client (RFC 6455), standard library only. Used for OBS and Home Assistant live updates.

Text frames only (JSON), client frames masked, ping answered, fragmented messages joined.
"""
import base64, hashlib, json, os, socket, ssl, struct, threading, urllib.parse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WSClosed(OSError):
    pass


class WebSocket:
    def __init__(self, url, timeout=6, headers=None):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("ws", "wss"):
            raise ValueError("WebSocket address must start with ws:// or wss://")
        host, port = u.hostname, u.port or (443 if u.scheme == "wss" else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        if u.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        self.sock, self._send_lock, self.closed = sock, threading.Lock(), False
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        hostport = host if u.port is None else f"{host}:{u.port}"
        lines = [f"GET {path} HTTP/1.1", f"Host: {hostport}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13", "User-Agent: vsdpad"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise WSClosed("connection closed during handshake")
            head += chunk
            if len(head) > 65536:
                raise WSClosed("handshake too long")
        head, self._buf = head.split(b"\r\n\r\n", 1)
        status, *hdrs = head.decode("latin-1").split("\r\n")
        if " 101 " not in status + " ":
            raise WSClosed(f"server refused the WebSocket ({status.strip()})")
        h = {k.strip().lower(): v.strip() for k, _, v in (x.partition(":") for x in hdrs)}
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if h.get("sec-websocket-accept") != want:
            raise WSClosed("bad WebSocket handshake")

    # ---- sending ----
    def _frame(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(head + mask + body)

    def send(self, obj):
        self._frame(0x1, (obj if isinstance(obj, str) else json.dumps(obj)).encode())

    # ---- receiving ----
    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                self.closed = True
                raise WSClosed("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self, timeout=None):
        """The next text message as parsed JSON (or str if not JSON). Raises socket.timeout / WSClosed."""
        self.sock.settimeout(timeout)
        parts = []
        while True:
            b1, b2 = self._read(2)
            fin, opcode, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > 64 * 1024 * 1024:
                raise WSClosed("message too big")
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                self.close()
                raise WSClosed("closed by server")
            if opcode == 0x9:
                self._frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            parts.append(data)
            if fin:
                text = b"".join(parts).decode("utf-8", "replace")
                try:
                    return json.loads(text)
                except ValueError:
                    return text

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self._frame(0x8, b"")
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
