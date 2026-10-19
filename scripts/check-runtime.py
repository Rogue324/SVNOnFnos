"""Audit bundled ELF architecture and DT_NEEDED closure on any build host."""
from pathlib import Path
import struct

root = Path(__file__).resolve().parents[1] / "fnos/app/runtime/root"
libraries = [root / "lib/x86_64-linux-gnu", root / "usr/lib/x86_64-linux-gnu", root / "usr/lib/x86_64-linux-gnu/gconv"]
count = 0
missing = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        continue
    count += 1
    assert data[4:6] == b"\x02\x01" and struct.unpack_from("<H", data, 18)[0] == 62, f"Not amd64 ELF: {path}"
    offset = struct.unpack_from("<Q", data, 40)[0]
    size, number = struct.unpack_from("<HH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * size) for i in range(number)]
    for section in sections:
        if section[1] != 6:  # SHT_DYNAMIC
            continue
        strings = sections[section[6]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            tag, value = struct.unpack_from("<qQ", data, offset)
            if tag == 1:  # DT_NEEDED
                start = strings[4] + value
                library = data[start:data.index(b"\0", start)].decode()
                if not any((folder / library).is_file() for folder in libraries):
                    missing.append((str(path.relative_to(root)), library))
assert not missing, f"Missing shared libraries: {missing}"
for binary in ("python3.11", "svn", "svnadmin", "svnlook", "svnserve"):
    assert (root / "usr/bin" / binary).is_file(), binary
assert (root / "lib/x86_64-linux-gnu/ld-linux-x86-64.so.2").is_file()
print(f"PASS: {count} amd64 ELF files; all DT_NEEDED libraries bundled. Linux execution still requires fnOS validation.")
