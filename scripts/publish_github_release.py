from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--asset", type=Path, action="append", required=True)
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GH_TOKEN or GITHUB_TOKEN is required")
    if not args.notes_file.is_file():
        raise SystemExit(f"Release notes not found: {args.notes_file}")
    for asset in args.asset:
        if not asset.is_file():
            raise SystemExit(f"Release asset not found: {asset}")

    api = f"https://api.github.com/repos/{args.repository}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ConstructionOwnerClassifier-Release",
    }
    session = requests.Session()
    release_id: int | None = None
    try:
        response = session.post(
            f"{api}/releases",
            headers=headers,
            timeout=30,
            json={
                "tag_name": args.tag,
                "target_commitish": "main",
                "name": args.title,
                "body": args.notes_file.read_text(encoding="utf-8-sig"),
                "draft": True,
                "prerelease": False,
            },
        )
        response.raise_for_status()
        release = response.json()
        release_id = int(release["id"])
        upload_url = str(release["upload_url"]).split("{", 1)[0]

        for asset in args.asset:
            with asset.open("rb") as stream:
                upload = session.post(
                    upload_url,
                    params={"name": asset.name},
                    headers={**headers, "Content-Type": "application/octet-stream"},
                    data=stream,
                    timeout=(10, 300),
                )
            upload.raise_for_status()
            if upload.json().get("name") != asset.name:
                raise RuntimeError(f"GitHub changed the asset name: {asset.name}")

        publish = session.patch(
            f"{api}/releases/{release_id}",
            headers=headers,
            timeout=30,
            json={"draft": False},
        )
        publish.raise_for_status()
        print(publish.json()["html_url"])
        return 0
    except Exception:
        if release_id is not None:
            session.delete(f"{api}/releases/{release_id}", headers=headers, timeout=30)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
