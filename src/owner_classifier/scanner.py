from __future__ import annotations

import os
from pathlib import Path


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SKIP_DIRECTORY_NAMES = {
    ".git", ".pytest_cache", ".venv", "venv", "__pycache__",
    "build", "dist", "node_modules",
}


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def scan_images(input_dir: str | Path, output_dir: str | Path | None = None) -> list[Path]:
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"输入目录不存在：{root}")

    excluded = Path(output_dir).expanduser().resolve() if output_dir else None
    excluded_key = _normalized(excluded) if excluded else ""
    seen: set[str] = set()
    images: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        relative_directories = path.relative_to(root).parts[:-1]
        if any(
            part in SKIP_DIRECTORY_NAMES
            or part.startswith(".pytest_tmp")
            or part.startswith("分类结果_")
            for part in relative_directories
        ):
            continue
        resolved = path.resolve()
        key = _normalized(resolved)
        if excluded and excluded != root and (key == excluded_key or key.startswith(excluded_key + os.sep)):
            continue
        if key in seen:
            continue
        seen.add(key)
        images.append(resolved)

    return sorted(images, key=lambda item: _normalized(item))
