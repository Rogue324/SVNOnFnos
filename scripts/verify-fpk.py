"""Check a built FPK's manifest against the source and requested target."""
import argparse
import hashlib
import io
from pathlib import Path
import tarfile


def manifest_fields(text):
    fields = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ValueError(f"Invalid manifest line: {line!r}")
        key = key.strip()
        if key in fields:
            raise ValueError(f"Duplicate manifest field: {key}")
        fields[key] = value.strip()
    return fields


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = manifest_fields((root / "fnos/manifest").read_text(encoding="utf-8"))
    with tarfile.open(args.package, "r:*") as archive:
        item = archive.extractfile("manifest")
        if item is None:
            raise ValueError("FPK does not contain a regular manifest file")
        package = manifest_fields(item.read().decode("utf-8"))
        app = archive.extractfile("app.tgz")
        if app is None:
            raise ValueError("FPK does not contain app.tgz")
        with tarfile.open(fileobj=io.BytesIO(app.read()), mode="r:gz") as inner:
            marker = inner.extractfile("runtime/architecture")
            if marker is None or marker.read() != (args.arch + "\n").encode("ascii"):
                raise ValueError("Bundled runtime architecture marker has an incorrect value or line ending")
    expected = {"appname": source["appname"], "version": source["version"],
                "platform": "x86" if args.arch == "amd64" else "arm"}
    for key, value in expected.items():
        if package.get(key) != value:
            raise ValueError(f"FPK {key}: expected {value!r}, got {package.get(key)!r}")
    with args.package.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = args.package.with_suffix(args.package.suffix + ".sha256")
    checksum.write_text(f"{digest}  {args.package.name}\n", encoding="ascii")
    print(f"Verified {args.package}: {expected}, sha256={digest}")


if __name__ == "__main__":
    main()
