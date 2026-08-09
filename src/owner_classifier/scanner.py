from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".webp", ".tif", ".tiff",
}
SKIP_DIRECTORY_NAMES = {
    ".git", ".pytest_cache", ".venv", "venv", "__pycache__",
    ".build-venv", "build", "dist", "node_modules",
}


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _source_list(input_sources: str | Path | Iterable[str | Path]) -> list[Path]:
    if isinstance(input_sources, (str, Path)):
        values = [input_sources]
    else:
        values = list(input_sources)
    if not values:
        raise ValueError("尚未选择图片或文件夹")
    return [Path(value).expanduser().resolve() for value in values]


def scan_images(
    input_sources: str | Path | Iterable[str | Path],
    output_dir: str | Path | None = None,
) -> list[Path]:
    sources = _source_list(input_sources)

    excluded = Path(output_dir).expanduser().resolve() if output_dir else None
    excluded_key = _normalized(excluded) if excluded else ""
    seen: set[str] = set()
    images: list[Path] = []

    def add_image(path: Path) -> None:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        resolved = path.resolve()
        key = _normalized(resolved)
        if key not in seen:
            seen.add(key)
            images.append(resolved)

    def skip_directory(name: str) -> bool:
        return (
            name in SKIP_DIRECTORY_NAMES
            or name.startswith((".pytest_tmp", ".pytest-tmp"))
            or name.startswith("分类结果_")
        )

    for source in sources:
        if not source.exists():
            raise ValueError(f"输入目录不存在：{source}")
        if source.is_file():
            add_image(source)
            continue
        if not source.is_dir():
            continue

        exclude_for_root = bool(
            excluded and excluded != source and excluded.is_relative_to(source)
        )
        for current, directories, filenames in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in directories:
                if skip_directory(name):
                    continue
                child_key = _normalized((current_path / name).resolve())
                if exclude_for_root and (
                    child_key == excluded_key or child_key.startswith(excluded_key + os.sep)
                ):
                    continue
                kept_directories.append(name)
            directories[:] = kept_directories

            for name in filenames:
                path = current_path / name
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    add_image(path)

    return sorted(images, key=lambda item: _normalized(item))
