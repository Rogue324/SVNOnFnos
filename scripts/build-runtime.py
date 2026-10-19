"""Bundle a private native Debian 12 amd64 Python/Subversion runtime.

Downloads only Debian main packages, validates their SHA256 against the official
HTTPS package index, follows Depends/Pre-Depends (not Recommends), and never runs
maintainer scripts or installs packages on the build host or NAS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MIRROR = "https://deb.debian.org/debian/"
INDEX = MIRROR + "dists/bookworm/main/binary-amd64/Packages.gz"
SECURITY_MIRROR = "https://security.debian.org/debian-security/"
SECURITY_INDEX = SECURITY_MIRROR + "dists/bookworm-security/main/binary-amd64/Packages.xz"


def version_compare(a, b):
    """Debian version ordering (epoch, upstream version, revision)."""
    def split(value):
        epoch, _, rest = value.partition(":")
        epoch, rest = (int(epoch), rest) if rest else (0, epoch)
        upstream, sep, revision = rest.rpartition("-")
        return epoch, upstream if sep else rest, revision if sep else "0"

    def compare_part(left, right):
        def order(c):
            if c == "~":
                return -1
            if not c:
                return 0
            return ord(c) if c.isalpha() else ord(c) + 256
        while left or right:
            while (left and not left[0].isdigit()) or (right and not right[0].isdigit()):
                x = left[0] if left and not left[0].isdigit() else ""
                y = right[0] if right and not right[0].isdigit() else ""
                if order(x) != order(y):
                    return 1 if order(x) > order(y) else -1
                left = left[1:] if x else left
                right = right[1:] if y else right
            x = re.match(r"\d*", left)[0]
            y = re.match(r"\d*", right)[0]
            if int(x or "0") != int(y or "0"):
                return 1 if int(x or "0") > int(y or "0") else -1
            left, right = left[len(x):], right[len(y):]
        return 0
    ae, au, ar = split(a)
    be, bu, br = split(b)
    if ae != be:
        return 1 if ae > be else -1
    return compare_part(au, bu) or compare_part(ar, br)


def fetch(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as stream, dest.with_suffix(".download").open("wb") as out:
                shutil.copyfileobj(stream, out)
            os.replace(dest.with_suffix(".download"), dest)
            return
        except OSError:
            if attempt == 2:
                raise


def parse_index(blob):
    packages = {}
    for paragraph in blob.split("\n\n"):
        entry = {}
        key = None
        for line in paragraph.splitlines():
            if line.startswith(" ") and key:
                entry[key] += " " + line.strip()
            elif ": " in line:
                key, value = line.split(": ", 1)
                entry[key] = value
        if "Package" in entry:
            packages[entry["Package"]] = entry
    return packages


def dependencies(packages):
    selected = {}

    def visit(key):
        if key in selected:
            return
        entry = packages[key]
        selected[key] = entry
        deps = ",".join(entry.get(k, "") for k in ("Depends", "Pre-Depends"))
        for group in deps.split(","):
            if not group.strip():
                continue
            alternatives = [re.split(r"[\s:(]", p.strip())[0] for p in group.split("|")]
            candidate = next((p for p in alternatives if p in packages), None)
            if candidate is None:
                raise RuntimeError(f"Cannot resolve {key}: {group}")
            visit(candidate)

    for key in ("python3.11", "subversion"):
        visit(key)
    return selected


def extract_deb(path, target, links):
    # Debian .deb is an ar archive containing data.tar.{xz,gz}.
    with path.open("rb") as stream:
        if stream.read(8) != b"!<arch>\n":
            raise RuntimeError(f"Invalid deb: {path}")
        while header := stream.read(60):
            if len(header) != 60:
                raise RuntimeError("Truncated ar header")
            filename = header[:16].decode().strip().rstrip("/")
            size = int(header[48:58])
            payload = stream.read(size)
            if size % 2:
                stream.read(1)
            if not filename.startswith("data.tar"):
                continue
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
                for item in archive:
                    rel = PurePosixPath(item.name)
                    if rel.is_absolute() or ".." in rel.parts:
                        raise RuntimeError(f"Unsafe tar member: {item.name}")
                    dest = target / rel
                    if item.isdir():
                        dest.mkdir(parents=True, exist_ok=True)
                    elif item.issym() or item.islnk():
                        link = PurePosixPath(item.linkname)
                        source = target / str(link).lstrip("/") if item.islnk() or link.is_absolute() else dest.parent / link
                        source = source.resolve()
                        if not source.is_relative_to(target.resolve()):
                            raise RuntimeError(f"Unsafe link: {item.name}")
                        links.append((dest, source))
                    elif item.isfile():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(item) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out)
                        os.chmod(dest, item.mode | 0o400)
            return
    raise RuntimeError(f"No data archive: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    cache = ROOT / ".tools" / "debian"
    runtime = ROOT / "fnos" / "app" / "runtime"
    marker = runtime / "packages.json"
    if marker.is_file() and not args.refresh:
        print(f"Native runtime already prepared: {runtime}")
        return
    index_path = cache / "Packages.gz"
    if not index_path.is_file() or args.refresh:
        fetch(INDEX, index_path)
    package_index = parse_index(gzip.decompress(index_path.read_bytes()).decode())
    for entry in package_index.values():
        entry["Mirror"] = MIRROR
    security_path = cache / "Security-Packages.xz"
    if not security_path.is_file() or args.refresh:
        fetch(SECURITY_INDEX, security_path)
    for key, entry in parse_index(lzma.decompress(security_path.read_bytes()).decode()).items():
        if key not in package_index or version_compare(entry["Version"], package_index[key]["Version"]) > 0:
            entry["Mirror"] = SECURITY_MIRROR
            package_index[key] = entry
    packages = dependencies(package_index)

    def download(entry):
        dest = cache / Path(entry["Filename"]).name
        if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != entry["SHA256"]:
            fetch(entry["Mirror"] + entry["Filename"], dest)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != entry["SHA256"]:
            raise RuntimeError(f"Checksum mismatch: {dest}")
        print(f"Verified {entry['Package']} {entry['Version']}", flush=True)
        return dest

    with ThreadPoolExecutor(max_workers=6) as pool:
        paths = list(pool.map(download, packages.values()))
    target = runtime / "root"
    if target.exists():
        # Rebuild only the generated runtime root, checked inside this workspace.
        if target.resolve() != (ROOT / "fnos" / "app" / "runtime" / "root").resolve():
            raise RuntimeError("Unexpected runtime target")
        for attempt in range(6):
            try:
                shutil.rmtree(target)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(1)
    target.mkdir(parents=True, exist_ok=True)
    links = []
    for path in paths:
        extract_deb(path, target, links)
    # Materialize links so Windows fnpack can package without symlink privileges.
    unresolved = links
    for _ in range(30):
        pending = []
        for dest, source in unresolved:
            if source.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                os.chmod(dest, source.stat().st_mode)
            elif source.is_dir():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, dest, dirs_exist_ok=True)
            else:
                pending.append((dest, source))
        if not pending or len(pending) == len(unresolved):
            unresolved = pending
            break
        unresolved = pending
    # Some optional links (manpages or system integration) target uninstalled files.
    missing_critical = [str(dest) for dest, _ in unresolved if "/lib" in dest.as_posix() or "/bin" in dest.as_posix()]
    if missing_critical:
        raise RuntimeError(f"Unresolved runtime links: {missing_critical}")
    # Optional desktop keyring providers are dynamically loaded by SVN, are not
    # dependencies of the CLI, and must not pull in NAS host Qt/GNOME libraries.
    for pattern in ("libsvn_auth_gnome_keyring*", "libsvn_auth_kwallet*"):
        for optional in (target / "usr/lib/x86_64-linux-gnu").glob(pattern):
            optional.unlink()
    bindir = runtime / "bin"
    bindir.mkdir(exist_ok=True)
    for tool in ("python3", "svn", "svnadmin", "svnlook", "svnserve"):
        executable = "python3.11" if tool == "python3" else tool
        text = '#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        text += 'export LC_ALL=C.UTF-8\nexport GCONV_PATH="$base/root/usr/lib/x86_64-linux-gnu/gconv"\n'
        if tool == "python3":
            text += 'export PYTHONHOME="$base/root/usr"\nexport PYTHONDONTWRITEBYTECODE=1\n'
        text += f'exec "$base/root/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2" --library-path "$base/root/lib/x86_64-linux-gnu:$base/root/usr/lib/x86_64-linux-gnu:$base/root/usr/lib/x86_64-linux-gnu/gconv" "$base/root/usr/bin/{executable}" "$@"\n'
        (bindir / tool).write_text(text, encoding="utf-8", newline="\n")
        os.chmod(bindir / tool, 0o755)
    manifest = {"architecture": "amd64", "distribution": "Debian 12 bookworm", "indexes": [INDEX, SECURITY_INDEX],
                "packages": [{k: entry[k] for k in ("Package", "Version", "Mirror", "Filename", "SHA256")} for entry in packages.values()]}
    marker.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Prepared native runtime: {len(packages)} packages, {sum(p.stat().st_size for p in target.rglob('*') if p.is_file()) / 1024**2:.1f} MiB")


if __name__ == "__main__":
    main()
