from __future__ import annotations

import base64
import hashlib
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

P = bytes.fromhex("3021300906052b0e03021a05000414")


def key() -> tuple[int, int]:
    root = ET.fromstring(os.environ["ETS_CONVERTER_KEY"])
    v = {c.tag.split("}")[-1]: int.from_bytes(base64.b64decode(c.text.strip()), "big") for c in root}
    return v["Modulus"], v["D"]


def entries(folder: Path) -> list[tuple[str, str]]:
    from pyuca import Collator

    c = Collator()
    out = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not str(p).lower().endswith(".chw"):
            out.append((str(p.relative_to(folder)), base64.b64encode(hashlib.sha1(p.read_bytes()).digest()).decode()))
    out.sort(key=lambda t: c.sort_key(t[0]))
    return out


def digest(folder: Path) -> bytes:
    return hashlib.sha1(",".join(f"{a}:{b}" for a, b in entries(folder)).encode()).digest()


def sign(d: bytes, n: int, e: int) -> str:
    k = (n.bit_length() + 7) // 8
    b = P + d
    x = b"\x00\x01" + b"\xff" * (k - 3 - len(b)) + b"\x00" + b
    return base64.b64encode(pow(int.from_bytes(x, "big"), e, n).to_bytes(k, "big")).decode()


def check(d: bytes, s: str, n: int) -> bool:
    k = (n.bit_length() + 7) // 8
    r = pow(int.from_bytes(base64.b64decode(s), "big"), 65537, n).to_bytes(k, "big")
    if not r.startswith(b"\x00\x01"):
        return False
    i = r[2:].find(b"\x00")
    return i >= 8 and r[2 : 2 + i].strip(b"\xff") == b"" and r[2 + i + 1 :] == P + d


def main() -> int:
    folder = Path(sys.argv[1])
    n, e = key()
    d = digest(folder)
    out = folder.parent / (folder.name + ".signature")
    print(d.hex())
    if len(sys.argv) > 2 and sys.argv[2] == "--check":
        ok = out.is_file() and check(d, out.read_text().strip(), n)
        print("VALID" if ok else "INVALID")
        return 0 if ok else 1
    if out.is_file() and check(d, out.read_text().strip(), n):
        print("unchanged:", out)
        return 0
    out.write_text(sign(d, n, e) + "\n")
    print("wrote:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
