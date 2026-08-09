from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import AppSettings, ClassificationRecord, RecordStatus, TaskSummary


DEFAULT_OWNERS = ["刘纪林", "吴万松", "吴绘其", "曹华兵", "郭成喜", "陈万智"]
DEFAULT_OWNER_ALIASES = {"曹华兵": ["曹华斌"]}


def default_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "ConstructionOwnerClassifier"


class Database:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_data_dir() / "classifier.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS owners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                aliases TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                input_dir TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                source_path TEXT NOT NULL,
                owner TEXT NOT NULL DEFAULT '',
                candidate_owner TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                local_confidence REAL NOT NULL DEFAULT 0,
                ocr_text TEXT NOT NULL DEFAULT '',
                ocr_blocks TEXT NOT NULL DEFAULT '',
                ocr_engine TEXT NOT NULL DEFAULT '',
                rotation INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                output_path TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT '',
                reviewed INTEGER NOT NULL DEFAULT 0,
                processed_at TEXT NOT NULL DEFAULT '',
                UNIQUE(task_id, source_path),
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );
            """
        )
        record_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(records)")}
        if "ocr_blocks" not in record_columns:
            self.connection.execute("ALTER TABLE records ADD COLUMN ocr_blocks TEXT NOT NULL DEFAULT ''")
        owner_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(owners)")}
        if "aliases" not in owner_columns:
            self.connection.execute("ALTER TABLE owners ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'")
        self._migrate_cao_owner()
        count = self.connection.execute("SELECT COUNT(*) FROM owners").fetchone()[0]
        if count == 0:
            now = datetime.now().isoformat(timespec="seconds")
            self.connection.executemany(
                "INSERT INTO owners(name, active, aliases, created_at) VALUES (?, 1, ?, ?)",
                [(name, json.dumps(DEFAULT_OWNER_ALIASES.get(name, []), ensure_ascii=False), now) for name in DEFAULT_OWNERS],
            )
        self.connection.commit()

    def _migrate_cao_owner(self) -> None:
        wrong = self.connection.execute("SELECT id, aliases FROM owners WHERE name = ?", ("曹华斌",)).fetchone()
        canonical = self.connection.execute("SELECT id, aliases FROM owners WHERE name = ?", ("曹华兵",)).fetchone()
        if not wrong:
            return
        if canonical:
            aliases = self._decode_aliases(canonical["aliases"])
            if "曹华斌" not in aliases:
                aliases.append("曹华斌")
            self.connection.execute(
                "UPDATE owners SET aliases = ? WHERE id = ?",
                (json.dumps(aliases, ensure_ascii=False), canonical["id"]),
            )
            self.connection.execute("DELETE FROM owners WHERE id = ?", (wrong["id"],))
        else:
            aliases = self._decode_aliases(wrong["aliases"])
            if "曹华斌" not in aliases:
                aliases.append("曹华斌")
            self.connection.execute(
                "UPDATE owners SET name = ?, aliases = ? WHERE id = ?",
                ("曹华兵", json.dumps(aliases, ensure_ascii=False), wrong["id"]),
            )

    @staticmethod
    def _decode_aliases(value: str) -> list[str]:
        try:
            data = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            data = []
        return [str(item).strip() for item in data if str(item).strip()]

    @staticmethod
    def _normalized_name(value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip().casefold()

    def _validate_owner(self, name: str, aliases: Iterable[str], exclude_id: int | None = None) -> tuple[str, list[str]]:
        canonical = name.strip()
        cleaned_aliases = list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))
        if not canonical:
            raise ValueError("责任人姓名不能为空")
        values = [canonical, *cleaned_aliases]
        normalized = [self._normalized_name(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("责任人姓名和别名不能重复")
        for owner_id, existing_name, existing_aliases in self.owner_rows():
            if owner_id == exclude_id:
                continue
            occupied = {
                self._normalized_name(value)
                for value in [existing_name, *existing_aliases]
            }
            if any(value in occupied for value in normalized):
                raise ValueError("责任人姓名或别名已被其他人员使用")
        return canonical, cleaned_aliases

    def owners(self, active_only: bool = True) -> list[str]:
        sql = "SELECT name FROM owners ORDER BY name COLLATE NOCASE"
        return [row["name"] for row in self.connection.execute(sql)]

    def owner_rows(self) -> list[tuple[int, str, list[str]]]:
        return [
            (row["id"], row["name"], self._decode_aliases(row["aliases"]))
            for row in self.connection.execute("SELECT id, name, aliases FROM owners ORDER BY id")
        ]

    def owner_alias_map(self) -> dict[str, list[str]]:
        return {name: aliases for _, name, aliases in self.owner_rows()}

    def add_owner(self, name: str, aliases: Iterable[str] = ()) -> None:
        normalized, cleaned_aliases = self._validate_owner(name, aliases)
        self.connection.execute(
            "INSERT INTO owners(name, active, aliases, created_at) VALUES (?, 1, ?, ?)",
            (normalized, json.dumps(cleaned_aliases, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
        )
        self.connection.commit()

    def update_owner(self, owner_id: int, name: str, aliases: Iterable[str] = ()) -> None:
        normalized, cleaned_aliases = self._validate_owner(name, aliases, exclude_id=owner_id)
        self.connection.execute(
            "UPDATE owners SET name = ?, aliases = ? WHERE id = ?",
            (normalized, json.dumps(cleaned_aliases, ensure_ascii=False), owner_id),
        )
        self.connection.commit()

    def delete_owner(self, owner_id: int) -> None:
        self.connection.execute("DELETE FROM owners WHERE id = ?", (owner_id,))
        self.connection.commit()

    def import_owners(self, names: Iterable[str]) -> int:
        count = 0
        for name in names:
            normalized = name.strip()
            if not normalized:
                continue
            self.add_owner(normalized)
            count += 1
        return count

    def load_settings(self) -> AppSettings:
        values = {
            row["key"]: json.loads(row["value"])
            for row in self.connection.execute("SELECT key, value FROM settings")
        }
        valid = {field.name for field in fields(AppSettings)}
        settings = AppSettings(**{key: value for key, value in values.items() if key in valid})
        return settings

    def save_settings(self, settings: AppSettings) -> None:
        for field in fields(settings):
            value = getattr(settings, field.name)
            self.connection.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (field.name, json.dumps(value, ensure_ascii=False)),
            )
        self.connection.commit()

    def create_task(self, input_dir: str, output_dir: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        cursor = self.connection.execute(
            "INSERT INTO tasks(input_dir, output_dir, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (input_dir, output_dir, "进行中", now, now),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def update_task_status(self, task_id: int, status: str) -> None:
        self.connection.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(timespec="seconds"), task_id),
        )
        self.connection.commit()

    def save_record(self, record: ClassificationRecord) -> int:
        if record.task_id is None:
            raise ValueError("记录缺少任务编号")
        values = (
            record.task_id, record.source_path, record.owner, record.candidate_owner,
            record.confidence, record.local_confidence,
            record.ocr_text, record.ocr_blocks, record.ocr_engine, record.rotation, str(record.status),
            record.output_path, record.error, record.sha256, int(record.reviewed),
            record.processed_at,
        )
        self.connection.execute(
            """
            INSERT INTO records(
                task_id, source_path, owner, candidate_owner, confidence,
                local_confidence, ocr_text, ocr_blocks, ocr_engine,
                rotation, status, output_path, error, sha256, reviewed, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, source_path) DO UPDATE SET
                owner=excluded.owner, candidate_owner=excluded.candidate_owner,
                confidence=excluded.confidence, local_confidence=excluded.local_confidence,
                ocr_text=excluded.ocr_text,
                ocr_blocks=excluded.ocr_blocks,
                ocr_engine=excluded.ocr_engine, rotation=excluded.rotation,
                status=excluded.status, output_path=excluded.output_path,
                error=excluded.error, sha256=excluded.sha256,
                reviewed=excluded.reviewed, processed_at=excluded.processed_at
            """,
            values,
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT id FROM records WHERE task_id = ? AND source_path = ?",
            (record.task_id, record.source_path),
        ).fetchone()
        record.record_id = int(row["id"])
        return record.record_id

    def latest_task(self) -> tuple[int, str, str, str] | None:
        row = self.connection.execute(
            "SELECT id, input_dir, output_dir, status FROM tasks ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return int(row["id"]), row["input_dir"], row["output_dir"], row["status"]

    def task_summaries(self, limit: int = 100) -> list[TaskSummary]:
        rows = self.connection.execute(
            """
            SELECT t.id, t.input_dir, t.output_dir, t.status, t.created_at, t.updated_at,
                   COUNT(r.id) AS total,
                   SUM(CASE WHEN r.status = ? THEN 1 ELSE 0 END) AS classified,
                   SUM(CASE WHEN r.status = ? THEN 1 ELSE 0 END) AS review,
                   SUM(CASE WHEN r.status = ? THEN 1 ELSE 0 END) AS unrecognized,
                   SUM(CASE WHEN r.status = ? THEN 1 ELSE 0 END) AS failed
            FROM tasks t
            LEFT JOIN records r ON r.task_id = t.id
            GROUP BY t.id
            ORDER BY t.id DESC
            LIMIT ?
            """,
            (
                str(RecordStatus.CLASSIFIED), str(RecordStatus.REVIEW),
                str(RecordStatus.UNRECOGNIZED), str(RecordStatus.FAILED), limit,
            ),
        ).fetchall()
        return [
            TaskSummary(
                task_id=int(row["id"]), input_dir=row["input_dir"], output_dir=row["output_dir"],
                status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"],
                total=int(row["total"] or 0), classified=int(row["classified"] or 0),
                review=int(row["review"] or 0), unrecognized=int(row["unrecognized"] or 0),
                failed=int(row["failed"] or 0),
            )
            for row in rows
        ]

    def task_records(self, task_id: int) -> list[ClassificationRecord]:
        rows = self.connection.execute(
            "SELECT * FROM records WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        records: list[ClassificationRecord] = []
        for row in rows:
            try:
                status = RecordStatus(row["status"])
            except ValueError:
                status = RecordStatus.FAILED
            records.append(
                ClassificationRecord(
                    source_path=row["source_path"], owner=row["owner"],
                    candidate_owner=row["candidate_owner"], confidence=row["confidence"],
                    local_confidence=row["local_confidence"],
                    ocr_text=row["ocr_text"], ocr_blocks=row["ocr_blocks"],
                    ocr_engine=row["ocr_engine"], rotation=row["rotation"],
                    status=status, output_path=row["output_path"], error=row["error"],
                    sha256=row["sha256"], reviewed=bool(row["reviewed"]),
                    processed_at=row["processed_at"], record_id=row["id"], task_id=row["task_id"],
                )
            )
        return records

    def close(self) -> None:
        self.connection.close()
