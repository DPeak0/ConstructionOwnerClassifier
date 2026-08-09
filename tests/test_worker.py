from __future__ import annotations

import threading
from pathlib import Path

import pytest

from owner_classifier.database import Database
from owner_classifier.models import AppSettings, ClassificationRecord, RecordStatus
from owner_classifier.worker import BatchWorker


def test_pause_stops_scheduling_new_recognition(monkeypatch, tmp_path: Path):
    database = Database(tmp_path / "test.db")
    output = tmp_path / "out"
    task_id = database.create_task(str(tmp_path), str(output))
    images = [tmp_path / f"{index}.jpg" for index in range(3)]
    for image in images:
        image.write_bytes(b"photo")

    worker = BatchWorker(
        images, {}, AppSettings(concurrency=1, concurrency_auto=False),
        database, task_id, output,
    )
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    calls: list[Path] = []

    def recognize(path: Path) -> ClassificationRecord:
        calls.append(path)
        if len(calls) == 1:
            first_started.set()
            release_first.wait(timeout=2)
        else:
            second_started.set()
        return ClassificationRecord(str(path), status=RecordStatus.REVIEW)

    monkeypatch.setattr(worker, "_recognize", recognize)
    runner = threading.Thread(target=worker.run)
    runner.start()
    assert first_started.wait(timeout=2)

    worker.pause()
    release_first.set()
    assert not second_started.wait(timeout=0.3)

    worker.resume()
    assert second_started.wait(timeout=2)
    runner.join(timeout=3)

    assert not runner.is_alive()
    assert calls == images
    database.close()


@pytest.mark.parametrize("status", [RecordStatus.UNRECOGNIZED, RecordStatus.FAILED])
def test_abnormal_record_keeps_batch_in_review_status(
    monkeypatch, tmp_path: Path, status: RecordStatus,
):
    database = Database(tmp_path / "test.db")
    output = tmp_path / "out"
    task_id = database.create_task(str(tmp_path), str(output))
    image = tmp_path / "abnormal.jpg"
    image.write_bytes(b"photo")
    worker = BatchWorker(
        [image], {}, AppSettings(concurrency=1, concurrency_auto=False),
        database, task_id, output,
    )
    monkeypatch.setattr(
        worker, "_recognize",
        lambda path: ClassificationRecord(str(path), status=status),
    )

    worker.run()

    assert database.latest_task()[-1] == "待复核"
    database.close()
