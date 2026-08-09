from pathlib import Path

import pytest

from owner_classifier.models import AppSettings, ClassificationRecord, RecordStatus
from owner_classifier.services import ClassificationService


def test_copy_classification_keeps_source_and_renames_duplicate(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    output = tmp_path / "out"
    service = ClassificationService(output, AppSettings(duplicate_policy="重命名"))

    first = service.classify(ClassificationRecord(str(source), owner="张三"), "张三")
    second = service.classify(ClassificationRecord(str(source), owner="张三"), "张三")

    assert source.exists()
    assert Path(first.output_path).name == "source.jpg"
    assert Path(second.output_path).name == "source_1.jpg"
    assert first.status == RecordStatus.CLASSIFIED
    assert first.sha256 == second.sha256


def test_classification_never_creates_reports_implicitly(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    output = tmp_path / "out"
    ClassificationService(output, AppSettings()).classify(
        ClassificationRecord(str(source), owner="张三"), "张三"
    )
    assert not list(output.glob("*.csv"))
    assert not list(output.glob("*.xlsx"))


def test_move_classification_verifies_destination_then_removes_source(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"important original")
    service = ClassificationService(tmp_path / "out", AppSettings(file_operation="移动"))

    record = service.classify(ClassificationRecord(str(source)), "未识别")

    destination = Path(record.output_path)
    assert not source.exists()
    assert destination.parent.name == "未识别"
    assert destination.read_bytes() == b"important original"
    assert record.status == RecordStatus.UNRECOGNIZED


def test_failed_move_keeps_original(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"must survive")
    service = ClassificationService(tmp_path / "out", AppSettings(file_operation="移动"))
    monkeypatch.setattr("owner_classifier.services.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk error")))

    with pytest.raises(OSError, match="disk error"):
        service.classify(ClassificationRecord(str(source)), "张三")

    assert source.read_bytes() == b"must survive"
    assert not list((tmp_path / "out" / "张三").glob("*.tmp"))


def test_interrupted_copy_keeps_source_and_removes_partial_file(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"must survive")
    service = ClassificationService(tmp_path / "out", AppSettings(file_operation="移动"))

    def interrupted_copy(source_stream, target_stream, length):
        target_stream.write(source_stream.read(4))
        raise OSError("interrupted")

    monkeypatch.setattr("owner_classifier.services.shutil.copyfileobj", interrupted_copy)
    with pytest.raises(OSError, match="interrupted"):
        service.classify(ClassificationRecord(str(source)), "张三")

    assert source.read_bytes() == b"must survive"
    assert not (tmp_path / "out" / "张三" / "source.jpg").exists()
    assert not list((tmp_path / "out" / "张三").glob("*.tmp"))


def test_source_delete_failure_leaves_verified_destination(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.jpg"
    content = b"must exist in at least one location"
    source.write_bytes(content)
    service = ClassificationService(tmp_path / "out", AppSettings(file_operation="移动"))
    original_unlink = Path.unlink

    def fail_source_delete(path, *args, **kwargs):
        if path == source:
            raise OSError("source locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_source_delete)
    with pytest.raises(OSError, match="source locked"):
        service.classify(ClassificationRecord(str(source)), "张三")

    assert source.read_bytes() == content
    assert (tmp_path / "out" / "张三" / "source.jpg").read_bytes() == content


def test_review_can_move_file_from_unrecognized_directory(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    settings = AppSettings(file_operation="移动")
    service = ClassificationService(tmp_path / "out", settings)
    record = service.classify(ClassificationRecord(str(source)), "未识别")
    old_output = Path(record.output_path)

    service.classify(record, "张三")

    assert not old_output.exists()
    assert Path(record.output_path).parent.name == "张三"
    assert Path(record.output_path).read_bytes() == b"photo"
