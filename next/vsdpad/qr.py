"""A small QR code maker (byte mode, error correction M, sizes 1-6: up to 106 characters), standard library only.
Used to show the phone remote's address so a phone camera can open it."""

# version -> (error correction codewords per block, [data codewords of each block])
BLOCKS = {1: (10, [16]), 2: (16, [28]), 3: (26, [44]), 4: (18, [32, 32]), 5: (24, [43, 43]), 6: (16, [27, 27, 27, 27])}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
ECC_M = 0b00

# ---- Reed-Solomon over GF(256) ----
_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i], _LOG[_x] = _x, _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs(data, n):
    gen = [1]
    for i in range(n):
        gen = [a ^ b for a, b in zip(gen + [0], [0] + [_mul(g, _EXP[i]) for g in gen])]
    rem = list(data) + [0] * n
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], c)
    return rem[len(data):]


def _codewords(text):
    data = text.encode("utf-8")
    for ver in range(1, 7):
        ec, blocks = BLOCKS[ver]
        cap = sum(blocks)
        if len(data) + 2 <= cap:
            break
    else:
        raise ValueError("too long for a QR code")
    bits = [0, 1, 0, 0] + [int(b) for b in f"{len(data):08b}"]
    for byte in data:
        bits += [int(b) for b in f"{byte:08b}"]
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    while len(words) < cap:
        words.append(pad[(len(words) - len(bits) // 8) % 2])
    split, i = [], 0
    for n in blocks:
        split.append(words[i:i + n])
        i += n
    eccs = [_rs(b, ec) for b in split]
    out = []
    for k in range(max(blocks)):
        out += [b[k] for b in split if k < len(b)]
    for k in range(ec):
        out += [e[k] for e in eccs]
    return ver, out


def _format_bits(mask):
    v = (ECC_M << 3) | mask
    rem = v << 10
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= 0x537 << (i - 10)
    return ((v << 10) | rem) ^ 0x5412


MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
         lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
         lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]


def _base(ver):
    n = 17 + 4 * ver
    m = [[None] * n for _ in range(n)]

    def finder(r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < n and 0 <= cc < n:
                    m[rr][cc] = 0 <= r <= 6 and 0 <= c <= 6 and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)
    for i in range(8, n - 8):
        m[6][i] = m[i][6] = i % 2 == 0
    pos = ALIGN[ver]
    for r in pos:
        for c in pos:
            if m[r][c] is not None:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = max(abs(dr), abs(dc)) != 1
    for i in range(9):   # reserve the format areas
        if m[8][i] is None:
            m[8][i] = False
        if m[i][8] is None:
            m[i][8] = False
    for i in range(8):
        m[8][n - 1 - i] = False
        m[n - 1 - i][8] = False
    m[n - 8][8] = True   # the dark module
    return m


def _place(m, words):
    n = len(m)
    reserved = [[v is not None for v in row] for row in m]
    bits = [(w >> (7 - i)) & 1 for w in words for i in range(8)]
    k, col, up = 0, n - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for r in rows:
            for c in (col, col - 1):
                if not reserved[r][c]:
                    m[r][c] = bool(bits[k]) if k < len(bits) else False
                    k += 1
        up = not up
        col -= 2
    return reserved


def _apply(m, reserved, mask):
    n = len(m)
    out = [row[:] for row in m]
    for r in range(n):
        for c in range(n):
            if not reserved[r][c] and MASKS[mask](r, c):
                out[r][c] = not out[r][c]
    f = _format_bits(mask)
    bit = lambda i: bool((f >> i) & 1)  # noqa: E731
    for i in range(6):
        out[i][8] = bit(i)
    out[7][8], out[8][8], out[8][7] = bit(6), bit(7), bit(8)
    for i in range(9, 15):
        out[8][14 - i] = bit(i)
    for i in range(8):
        out[8][n - 1 - i] = bit(i)
    for i in range(8, 15):
        out[n - 15 + i][8] = bit(i)
    return out


def _penalty(m):
    n, p = len(m), 0
    for lines in (m, [list(col) for col in zip(*m)]):
        for row in lines:
            run, prev = 0, None
            for v in row:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        p += run - 2
                    run, prev = 1, v
            if run >= 5:
                p += run - 2
            s = "".join("1" if v else "0" for v in row)
            for pat in ("10111010000", "00001011101"):
                i = s.find(pat)
                while i != -1:
                    p += 40
                    i = s.find(pat, i + 1)
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                p += 3
    dark = sum(v for row in m for v in row)
    p += abs(dark * 20 // (n * n) - 10) * 10
    return p


def matrix(text, mask=None):
    """The QR code for text as rows of True (dark) / False."""
    ver, words = _codewords(text)
    m = _base(ver)
    reserved = _place(m, words)
    if mask is not None:
        return _apply(m, reserved, mask)
    return min((_apply(m, reserved, k) for k in range(8)), key=_penalty)


def svg(text, scale=6, border=4):
    m = matrix(text)
    n = len(m) + 2 * border
    path = "".join(f"M{c + border},{r + border}h1v1h-1z" for r, row in enumerate(m) for c, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" height="{n * scale}" '
            f'shape-rendering="crispEdges"><rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
