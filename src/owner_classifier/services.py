from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from .models import AppSettings, ClassificationRecord, RecordStatus


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ClassificationService:
    def __init__(self, output_root: str | Path, settings: AppSettings) -> None:
        self.output_root = Path(output_root)
        self.settings = settings
        self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def create_session_dir(parent: str | Path) -> Path:
        root = Path(parent) / f"分类结果_{datetime.now():%Y%m%d_%H%M%S}"
        suffix = 1
        candidate = root
        while candidate.exists():
            candidate = Path(f"{root}_{suffix}")
            suffix += 1
        candidate.mkdir(parents=True)
        return candidate

    @staticmethod
    def _source(record: ClassificationRecord) -> Path:
        source = Path(record.source_path)
        if not source.is_file() and record.output_path:
            source = Path(record.output_path)
        if not source.is_file():
            raise FileNotFoundError(f"原图和已分类文件均不存在：{record.source_path}")
        return source

    def _destination(self, record: ClassificationRecord, owner: str) -> Path:
        directory = self.output_root / owner
        directory.mkdir(parents=True, exist_ok=True)
        source = self._source(record)
        candidate = directory / source.name
        if not candidate.exists():
            return candidate
        if self.settings.duplicate_policy == "跳过":
            return candidate
        if self.settings.duplicate_policy == "覆盖":
            return candidate
        index = 1
        while True:
            renamed = directory / f"{source.stem}_{index}{source.suffix}"
            if not renamed.exists():
                return renamed
            index += 1

    def classify(self, record: ClassificationRecord, owner: str | None = None) -> ClassificationRecord:
        target_owner = (owner or record.owner or record.candidate_owner).strip()
        if not target_owner:
            target_owner = "未识别"
        destination = self._destination(record, target_owner)
        source = self._source(record)
        source_hash = record.sha256 or sha256_file(source)
        if destination.exists() and self.settings.duplicate_policy == "跳过":
            if self.settings.file_operation == "移动" and sha256_file(destination) != source_hash:
                raise FileExistsError(f"目标文件已存在且内容不同，原文件已保留：{destination}")
            record.output_path = str(destination)
            if self.settings.file_operation == "移动" and source.resolve() != destination.resolve():
                source.unlink()
        else:
            if self.settings.file_operation == "移动":
                self._verified_move(source, destination, source_hash)
            else:
                self._verified_copy(source, destination, source_hash)
            record.output_path = str(destination)
        record.sha256 = source_hash
        record.owner = "" if target_owner == "未识别" else target_owner
        record.status = RecordStatus.UNRECOGNIZED if target_owner == "未识别" else RecordStatus.CLASSIFIED
        record.processed_at = datetime.now().isoformat(timespec="seconds")
        return record

    @staticmethod
    def _verified_copy(source: Path, destination: Path, source_hash: str) -> None:
        if source.resolve() == destination.resolve():
            return
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
            ) as target:
                temporary = Path(target.name)
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            shutil.copystat(source, temporary)
            if temporary.stat().st_size != source.stat().st_size or sha256_file(temporary) != source_hash:
                raise OSError("写入后的文件校验失败，源文件已保留")
            os.replace(temporary, destination)
            temporary = None
            if sha256_file(destination) != source_hash:
                raise OSError("目标文件校验失败，源文件已保留")
        except Exception:
            if temporary and temporary.exists():
                temporary.unlink()
            raise

    @classmethod
    def _verified_move(cls, source: Path, destination: Path, source_hash: str) -> None:
        if source.resolve() == destination.resolve():
            return
        cls._verified_copy(source, destination, source_hash)
        source.unlink()
