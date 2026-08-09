from pathlib import Path

import pytest

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
        candidate_owners=[("张三", 0.80), ("李四", 0.63)],
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
    assert loaded[0].candidate_owners == [("张三", 0.8), ("李四", 0.63)]
    database.close()


def test_owner_aliases_are_unique_and_owner_can_be_modified_or_deleted(tmp_path: Path):
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


def test_database_rejects_owner_names_that_are_unsafe_as_directories(tmp_path: Path):
    database = Database(tmp_path / "test.db")

    with pytest.raises(ValueError, match="责任人姓名"):
        database.add_owner(r"..\escaped")

    database.close()


def test_legacy_cao_alias_is_removed_once_without_merging_distinct_owner(tmp_path: Path):
    import sqlite3

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE owners (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 1, aliases TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
    connection.execute("INSERT INTO owners(name, active, aliases, created_at) VALUES ('曹华兵', 1, '[\"曹华斌\"]', '2026-01-01')")
    connection.execute("INSERT INTO owners(name, active, aliases, created_at) VALUES ('曹华斌', 1, '[]', '2026-01-01')")
    connection.commit()
    connection.close()

    database = Database(path)
    assert {"曹华兵", "曹华斌"}.issubset(database.owners())
    assert database.owner_alias_map()["曹华兵"] == []
    database.delete_owner(next(row[0] for row in database.owner_rows() if row[1] == "曹华斌"))
    owner_id = next(row[0] for row in database.owner_rows() if row[1] == "曹华兵")
    database.update_owner(owner_id, "曹华兵", ["曹华斌"])
    database.close()

    reopened = Database(path)
    assert reopened.owner_alias_map()["曹华兵"] == ["曹华斌"]
    reopened.close()
