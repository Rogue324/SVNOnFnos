"""Audit bundled ELF architecture and DT_NEEDED closure on any build host."""
import argparse
import json
from pathlib import Path
import struct

architectures = {
    "amd64": ("x86_64-linux-gnu", "ld-linux-x86-64.so.2", 62),
    "arm64": ("aarch64-linux-gnu", "ld-linux-aarch64.so.1", 183),
}
aliases = {"x86_64": "amd64", "aarch64": "arm64"}
parser = argparse.ArgumentParser()
parser.add_argument("--arch", choices=(*architectures, *aliases),
                    help="target architecture (default: manifest architecture)")
args = parser.parse_args()
runtime = Path(__file__).resolve().parents[1] / "fnos/app/runtime"
manifest = json.loads((runtime / "packages.json").read_text(encoding="utf-8"))
manifest_arch = manifest.get("architecture")
arch = aliases.get(args.arch, args.arch) if args.arch else manifest_arch
if arch not in architectures:
    parser.error(f"Unsupported manifest architecture: {arch}")
if arch != manifest_arch:
    parser.error(f"Requested architecture {arch} does not match manifest {manifest_arch}")
architecture_file = (runtime / "architecture").read_text(encoding="utf-8").strip()
if architecture_file != manifest_arch:
    parser.error(f"Architecture file {architecture_file!r} does not match manifest {manifest_arch}")
triplet, loader, machine = architectures[arch]
root = runtime / "root"
libraries = [root / "lib" / triplet, root / "usr/lib" / triplet, root / "usr/lib" / triplet / "gconv"]
count = 0
missing = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        continue
    count += 1
    assert data[4:6] == b"\x02\x01" and struct.unpack_from("<H", data, 18)[0] == machine, f"Not {arch} ELF: {path}"
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
assert (root / "lib" / triplet / loader).is_file()
for tool in ("python3", "svn", "svnadmin", "svnlook", "svnserve"):
    executable = "python3.11" if tool == "python3" else tool
    expected_exec = f'exec "$base/root/lib/{triplet}/{loader}" --library-path "$base/root/lib/{triplet}:$base/root/usr/lib/{triplet}:$base/root/usr/lib/{triplet}/gconv" "$base/root/usr/bin/{executable}" "$@"'
    expected_gconv = f'export GCONV_PATH="$base/root/usr/lib/{triplet}/gconv"'
    wrapper = (runtime / "bin" / tool).read_text(encoding="utf-8").splitlines()
    if expected_exec not in wrapper or expected_gconv not in wrapper:
        raise RuntimeError(f"Wrapper does not match {arch} runtime: {tool}")
print(f"PASS: {count} {arch} ELF files; all DT_NEEDED libraries bundled. Linux execution still requires fnOS validation.")
