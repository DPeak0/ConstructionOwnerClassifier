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


def test_no_watermark_is_classified_into_its_own_directory(tmp_path: Path):
    source = tmp_path / "clean.jpg"
    source.write_bytes(b"photo")
    record = ClassificationService(tmp_path / "out", AppSettings()).classify(
        ClassificationRecord(str(source), status=RecordStatus.NO_WATERMARK), "无水印"
    )
    assert Path(record.output_path).parent.name == "无水印"
    assert record.status == RecordStatus.NO_WATERMARK
    assert record.owner == ""


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

    service.reclassify(record, "张三")

    assert not old_output.exists()
    assert Path(record.output_path).parent.name == "张三"
    assert Path(record.output_path).read_bytes() == b"photo"


def test_classified_file_can_be_reclassified_without_leaving_old_copy(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    service = ClassificationService(tmp_path / "out", AppSettings())
    record = service.classify(ClassificationRecord(str(source)), "张三")
    old_output = Path(record.output_path)

    service.reclassify(record, "李四")
    new_output = Path(record.output_path)

    assert new_output.parent.name == "李四"
    assert new_output.read_bytes() == b"photo"
    assert not old_output.exists()


def test_reclassifying_to_same_owner_reuses_current_output_name(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    service = ClassificationService(tmp_path / "out", AppSettings())
    record = service.classify(ClassificationRecord(str(source)), "张三")
    first_output = record.output_path

    service.reclassify(record, "张三")

    assert record.output_path == first_output


def test_failed_reclassification_preserves_old_classified_file(tmp_path: Path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    output = tmp_path / "out"
    service = ClassificationService(output, AppSettings(duplicate_policy="跳过"))
    record = service.classify(ClassificationRecord(str(source)), "张三")
    old_output = Path(record.output_path)
    conflicting = output / "李四" / source.name
    conflicting.parent.mkdir(parents=True)
    conflicting.write_bytes(b"different")

    with pytest.raises(FileExistsError):
        service.reclassify(record, "李四")

    assert old_output.read_bytes() == b"photo"


@pytest.mark.parametrize("owner", ["../escaped", r"..\escaped", r"C:\escaped", "CON"])
def test_classification_rejects_unsafe_owner_directory_names(tmp_path: Path, owner: str):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    output = tmp_path / "out"
    service = ClassificationService(output, AppSettings(file_operation="移动"))

    with pytest.raises(ValueError, match="责任人姓名"):
        service.classify(ClassificationRecord(str(source)), owner)

    assert source.read_bytes() == b"photo"
    assert not (tmp_path / "escaped").exists()
