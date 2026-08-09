from pathlib import Path

from owner_classifier.database import Database
from owner_classifier.models import AppSettings, ClassificationRecord, RecordStatus


def test_database_persists_settings_owners_and_records(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    database.add_owner("张三", ["张叁"])
    settings = AppSettings(auto_threshold=0.88, concurrency=2, file_operation="移动", recognition_keywords=["现场负责人"])
    database.save_settings(settings)
    task_id = database.create_task(str(tmp_path), str(tmp_path / "out"))
    record = ClassificationRecord(
        source_path=str(tmp_path / "a.jpg"), task_id=task_id,
        candidate_owner="张三", confidence=0.80, status=RecordStatus.REVIEW,
        ocr_blocks="[]",
    )
    database.save_record(record)

    assert "张三" in database.owners()
    assert database.owner_alias_map()["张三"] == ["张叁"]
    assert database.load_settings().auto_threshold == 0.88
    assert database.load_settings().file_operation == "移动"
    assert database.load_settings().recognition_keywords == ["现场负责人"]
    summaries = database.task_summaries()
    assert summaries[0].total == 1
    assert summaries[0].review == 1
    loaded = database.task_records(task_id)
    assert len(loaded) == 1
    assert loaded[0].status == RecordStatus.REVIEW
    database.close()


def test_owner_aliases_are_unique_and_owner_can_be_modified_or_deleted(tmp_path: Path):
    import pytest

    database = Database(tmp_path / "test.db")
    database.add_owner("张三", ["张叁"])
    with pytest.raises(ValueError):
        database.add_owner("李四", ["张叁"])
    owner_id = next(row[0] for row in database.owner_rows() if row[1] == "张三")
    database.update_owner(owner_id, "张三丰", ["张三"])
    assert database.owner_alias_map()["张三丰"] == ["张三"]
    database.delete_owner(owner_id)
    assert "张三丰" not in database.owners()
    database.close()


def test_old_cao_name_migrates_to_canonical_alias(tmp_path: Path):
    import sqlite3

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE owners (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)")
    connection.execute("INSERT INTO owners(name, active, created_at) VALUES ('曹华斌', 1, '2026-01-01')")
    connection.commit()
    connection.close()

    database = Database(path)
    assert "曹华兵" in database.owners()
    assert database.owner_alias_map()["曹华兵"] == ["曹华斌"]
    database.close()
