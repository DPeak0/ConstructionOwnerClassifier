from __future__ import annotations

import hashlib

import pytest
import requests

from owner_classifier.updater import (
    MANIFEST_URLS,
    RELEASE_MANIFEST_URL,
    UpdateError,
    UpdateInfo,
    UpdateService,
    version_key,
)


def manifest(content: bytes, version: str = "1.2.0") -> dict:
    return {
        "schema": 1,
        "repository": "DPeak0/ConstructionOwnerClassifier",
        "version": version,
        "release_url": f"https://github.com/DPeak0/ConstructionOwnerClassifier/releases/tag/v{version}",
        "published_at": "2026-08-09T00:00:00Z",
        "notes": "修复测试",
        "installer": {
            "name": f"施工责任人图片分类器-Setup-{version}.exe",
            "url": (
                "https://github.com/DPeak0/ConstructionOwnerClassifier/releases/download/"
                f"v{version}/施工责任人图片分类器-Setup-{version}.exe"
            ),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    }


class FakeResponse:
    def __init__(self, payload=None, content: bytes = b"", status: int = 200):
        self.payload = payload
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.payload

    def iter_content(self, chunk_size: int):
        yield self.content


class FakeSession:
    def __init__(self, content: bytes):
        self.content = content
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "unavailable" in url or (
            kwargs.get("stream") and url.startswith("https://github.com/")
        ):
            raise requests.ConnectionError("blocked")
        if kwargs.get("stream"):
            return FakeResponse(content=self.content)
        return FakeResponse(payload=manifest(self.content))


def test_update_check_falls_back_without_github_api(tmp_path):
    content = b"installer"
    session = FakeSession(content)
    service = UpdateService(
        session=session,
        manifest_urls=("https://unavailable.invalid/latest.json", "https://cdn.example/latest.json"),
        update_dir=tmp_path,
    )
    info = service.check_for_update("1.1.1")
    assert info and info.version == "1.2.0"
    assert len(session.calls) == 2
    assert service.check_for_update("1.2.0") is None


def test_manifest_sources_include_verified_china_relays():
    assert f"https://gh-proxy.com/{RELEASE_MANIFEST_URL}" in MANIFEST_URLS
    assert f"https://ghproxy.net/{RELEASE_MANIFEST_URL}" in MANIFEST_URLS
    assert all("ghp.ci" not in url and "moeyy.cn" not in url for url in MANIFEST_URLS)


def test_update_download_uses_relay_and_verifies_hash(tmp_path):
    content = b"verified installer content"
    session = FakeSession(content)
    info = UpdateInfo.from_manifest(manifest(content))
    service = UpdateService(
        session=session,
        manifest_urls=(),
        download_relays=("https://relay.example/",),
        update_dir=tmp_path,
    )
    progress = []
    path = service.download(info, lambda current, total: progress.append((current, total)))
    assert path.read_bytes() == content
    assert progress[-1] == (len(content), len(content))
    assert any(url.startswith("https://relay.example/") for url in session.calls)


def test_update_rejects_tampered_download(tmp_path):
    expected = b"expected"
    info = UpdateInfo.from_manifest(manifest(expected))
    session = FakeSession(b"tampered")
    service = UpdateService(
        session=session, manifest_urls=(), download_relays=(), update_dir=tmp_path,
    )
    with pytest.raises(UpdateError):
        service.download(info)
    assert not list(tmp_path.glob("*.exe"))


def test_version_comparison_is_numeric():
    assert version_key("v1.10.0") > version_key("1.9.9")
