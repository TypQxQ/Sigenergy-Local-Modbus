"""Build HACS's installation ZIP from the exact committed integration files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

DOMAIN_PATH = "custom_components/sigen/"


def build_release(repository: Path, tag: str, output: Path) -> dict[str, str]:
    """Package HEAD, rejecting inconsistent release metadata before writing."""
    tree = subprocess.run(
        ["git", "ls-tree", "-rz", "HEAD", "--", "hacs.json", DOMAIN_PATH],
        cwd=repository, check=True, capture_output=True,
    ).stdout
    files = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ValueError(f"Unsupported source entry: {name!r}")
        # Read raw blobs: git archive can apply Windows newline conversion.
        files[name.decode("utf-8")] = subprocess.run(
            ["git", "cat-file", "blob", object_id.decode("ascii")],
            cwd=repository, check=True, capture_output=True,
        ).stdout

    hacs = json.loads(files["hacs.json"])
    manifest = json.loads(files[f"{DOMAIN_PATH}manifest.json"])
    version = manifest["version"]
    if not re.fullmatch(r"\d+(?:\.\d+){2,3}(?:(?:a|b|rc)\d+)?", version):
        raise ValueError(f"Unsupported release version: {version}")
    if tag != f"v.{version}":
        raise ValueError(f"Tag {tag!r} does not match manifest version {version!r}")
    if manifest["domain"] != "sigen":
        raise ValueError("Unexpected integration domain")
    if hacs.get("zip_release") is not True or hacs.get("filename") != "sigen.zip":
        raise ValueError("hacs.json must select the sigen.zip release asset")

    payload = {
        name.removeprefix(DOMAIN_PATH): content
        for name, content in files.items() if name.startswith(DOMAIN_PATH)
    }
    if "__init__.py" not in payload:
        raise ValueError("Missing integration entry point")
    if any("__pycache__" in Path(name).parts or name.endswith(".pyc") for name in payload):
        raise ValueError("Generated Python cache must not be committed in the package")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3  # keep archive metadata identical on Windows/Linux
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, content)

    with zipfile.ZipFile(output) as package:
        if package.testzip() or set(package.namelist()) != set(payload):
            raise ValueError("ZIP contents do not match the integration")
        if any(package.read(name) != content for name, content in payload.items()):
            raise ValueError("ZIP files differ from the committed integration")

    return {
        "version": version,
        "prerelease": str(bool(re.search(r"(?:a|b|rc)\d+$", version))).lower(),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "files": str(len(payload)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=Path("dist/sigen.zip"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    result = build_release(Path(__file__).resolve().parents[1], args.tag, args.output)
    print(json.dumps(result))
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
