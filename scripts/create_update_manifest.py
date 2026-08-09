from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", default="DPeak0/ConstructionOwnerClassifier")
    parser.add_argument("--notes-file", type=Path)
    args = parser.parse_args()

    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"Invalid release version: {args.version}")
    if not args.installer.is_file():
        raise SystemExit(f"Installer not found: {args.installer}")
    if not args.installer.name.endswith(f"-Setup-{version}.exe"):
        raise SystemExit("Installer filename does not match the release version")

    digest = hashlib.sha256()
    with args.installer.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    notes = ""
    if args.notes_file and args.notes_file.is_file():
        notes = args.notes_file.read_text(encoding="utf-8-sig").strip()
    tag = f"v{version}"
    name = args.installer.name
    manifest = {
        "schema": 1,
        "repository": args.repository,
        "version": version,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "release_url": f"https://github.com/{args.repository}/releases/tag/{tag}",
        "notes": notes,
        "installer": {
            "name": name,
            "url": (
                f"https://github.com/{args.repository}/releases/download/"
                f"{tag}/{quote(name)}"
            ),
            "size": args.installer.stat().st_size,
            "sha256": digest.hexdigest(),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
