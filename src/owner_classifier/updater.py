from __future__ import annotations

import hashlib
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from .database import default_data_dir


REPOSITORY = "DPeak0/ConstructionOwnerClassifier"
RELEASE_MANIFEST_URL = (
    "https://github.com/DPeak0/ConstructionOwnerClassifier/releases/latest/download/update-manifest.json"
)
MANIFEST_URLS = (
    "https://cdn.jsdelivr.net/gh/DPeak0/ConstructionOwnerClassifier@release-channel/update/latest.json",
    "https://fastly.jsdelivr.net/gh/DPeak0/ConstructionOwnerClassifier@release-channel/update/latest.json",
    "https://gcore.jsdelivr.net/gh/DPeak0/ConstructionOwnerClassifier@release-channel/update/latest.json",
    "https://raw.githubusercontent.com/DPeak0/ConstructionOwnerClassifier/release-channel/update/latest.json",
    RELEASE_MANIFEST_URL,
)
DOWNLOAD_RELAYS = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://ghfast.top/",
)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    installer_name: str
    installer_url: str
    sha256: str
    size: int
    release_url: str = ""
    published_at: str = ""
    notes: str = ""

    @classmethod
    def from_manifest(cls, payload: dict) -> "UpdateInfo":
        if payload.get("repository") != REPOSITORY:
            raise UpdateError("更新清单的仓库标识无效")
        installer = payload.get("installer")
        if not isinstance(installer, dict):
            raise UpdateError("更新清单缺少安装包信息")
        version = str(payload.get("version", "")).lstrip("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise UpdateError("更新版本号格式无效")
        name = Path(str(installer.get("name", ""))).name
        url = str(installer.get("url", ""))
        sha256 = str(installer.get("sha256", "")).lower()
        size = int(installer.get("size", 0))
        parsed = urlparse(url)
        if not name.lower().endswith(".exe") or name != str(installer.get("name", "")):
            raise UpdateError("更新安装包名称无效")
        if not name.endswith(f"-Setup-{version}.exe"):
            raise UpdateError("更新安装包名称与版本号不一致")
        if parsed.scheme != "https" or parsed.hostname != "github.com":
            raise UpdateError("更新安装包必须来自正式 GitHub Release")
        if f"/{REPOSITORY}/releases/download/" not in parsed.path:
            raise UpdateError("更新安装包地址与发布仓库不匹配")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise UpdateError("更新清单缺少有效的 SHA-256")
        if size <= 0 or size > 300 * 1024 * 1024:
            raise UpdateError("更新安装包大小无效")
        return cls(
            version=version,
            installer_name=name,
            installer_url=url,
            sha256=sha256,
            size=size,
            release_url=str(payload.get("release_url", "")),
            published_at=str(payload.get("published_at", "")),
            notes=str(payload.get("notes", "")),
        )


def version_key(version: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"无效版本号：{version}")
    return tuple(int(part) for part in match.groups())


class UpdateService:
    def __init__(
        self,
        session: requests.Session | None = None,
        manifest_urls: tuple[str, ...] = MANIFEST_URLS,
        download_relays: tuple[str, ...] = DOWNLOAD_RELAYS,
        update_dir: str | Path | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.manifest_urls = manifest_urls
        self.download_relays = download_relays
        self.update_dir = Path(update_dir) if update_dir else default_data_dir() / "updates"
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "ConstructionOwnerClassifier-Updater",
        }

    def check_for_update(self, current_version: str) -> UpdateInfo | None:
        errors: list[str] = []
        cache_buster = int(time.time() // 300)

        def fetch(source: str) -> UpdateInfo:
            separator = "&" if "?" in source else "?"
            response = self.session.get(
                f"{source}{separator}check={cache_buster}",
                headers=self.headers,
                timeout=(5, 12),
            )
            response.raise_for_status()
            return UpdateInfo.from_manifest(response.json())

        valid_current = False
        with ThreadPoolExecutor(max_workers=max(1, len(self.manifest_urls))) as executor:
            futures = {executor.submit(fetch, source): source for source in self.manifest_urls}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    info = future.result()
                    if version_key(info.version) > version_key(current_version):
                        return info
                    valid_current = True
                except (requests.RequestException, ValueError, TypeError, UpdateError) as exc:
                    errors.append(f"{urlparse(source).hostname}: {exc}")
        if valid_current:
            return None
        detail = "; ".join(errors[-3:])
        raise UpdateError(f"无法连接更新服务器。请稍后重试。{detail}")

    def download(
        self,
        info: UpdateInfo,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        self.update_dir.mkdir(parents=True, exist_ok=True)
        target = self.update_dir / info.installer_name
        partial = target.with_suffix(target.suffix + ".part")
        sources = (info.installer_url,) + tuple(
            f"{relay}{info.installer_url}" for relay in self.download_relays
        )
        errors: list[str] = []
        for source in sources:
            digest = hashlib.sha256()
            downloaded = 0
            try:
                response = self.session.get(
                    source,
                    headers={"User-Agent": self.headers["User-Agent"]},
                    timeout=(6, 45),
                    stream=True,
                    allow_redirects=True,
                )
                response.raise_for_status()
                with partial.open("wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(downloaded, info.size)
                if downloaded != info.size:
                    raise UpdateError(f"文件大小不一致：{downloaded}/{info.size}")
                if digest.hexdigest().lower() != info.sha256:
                    raise UpdateError("SHA-256 校验失败")
                partial.replace(target)
                return target
            except (OSError, requests.RequestException, UpdateError) as exc:
                errors.append(f"{urlparse(source).hostname}: {exc}")
                partial.unlink(missing_ok=True)
        raise UpdateError("更新包下载失败。" + "; ".join(errors[-3:]))

    @staticmethod
    def launch_installer(path: str | Path) -> None:
        installer = Path(path)
        if not installer.is_file():
            raise UpdateError("已下载的安装包不存在")
        subprocess.Popen([str(installer), "/SP-"], close_fds=True)
