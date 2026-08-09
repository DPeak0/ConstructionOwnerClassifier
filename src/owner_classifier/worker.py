from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .database import Database
from .engine import RecognitionEngine
from .models import AppSettings, ClassificationRecord, RecordStatus
from .ocr import create_local_provider
from .services import ClassificationService


class BatchWorker(QThread):
    record_ready = Signal(object, int)
    progress_changed = Signal(int, int)
    batch_finished = Signal(bool, str)
    batch_error = Signal(str)

    def __init__(
        self,
        images: list[Path],
        owners: dict[str, list[str]],
        settings: AppSettings,
        database: Database,
        task_id: int,
        output_root: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.images = images
        self.owners = owners
        self.settings = settings
        self.database = database
        self.task_id = task_id
        self.output_root = output_root
        self._cancelled = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._thread_local = threading.local()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._resume.set()

    def _engine(self) -> RecognitionEngine:
        engine = getattr(self._thread_local, "engine", None)
        if engine is None:
            engine = RecognitionEngine(
                create_local_provider(prefer_paddle=True),
                self.owners,
                self.settings,
            )
            self._thread_local.engine = engine
        return engine

    def _recognize(self, path: Path) -> ClassificationRecord:
        return self._engine().classify(path)

    def run(self) -> None:
        service = ClassificationService(self.output_root, self.settings)
        records: list[ClassificationRecord] = []
        completed = 0
        futures: dict[Future[ClassificationRecord], int] = {}
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(self.settings.concurrency, 4))) as executor:
                for index, path in enumerate(self.images):
                    if self._cancelled.is_set():
                        break
                    futures[executor.submit(self._recognize, path)] = index

                for future in as_completed(futures):
                    if self._cancelled.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    self._resume.wait()
                    if self._cancelled.is_set():
                        for pending in futures:
                            pending.cancel()
                        break
                    index = futures[future]
                    try:
                        record = future.result()
                        record.task_id = self.task_id
                        if record.status == RecordStatus.CONFIRMED:
                            service.classify(record, record.owner)
                        elif record.status == RecordStatus.UNRECOGNIZED:
                            service.classify(record, "未识别")
                        self.database.save_record(record)
                        records.append(record)
                        self.record_ready.emit(record, index)
                    except Exception as exc:
                        record = ClassificationRecord(
                            source_path=str(self.images[index]), task_id=self.task_id,
                            status=RecordStatus.FAILED, error=str(exc),
                        )
                        self.database.save_record(record)
                        records.append(record)
                        self.record_ready.emit(record, index)
                    completed += 1
                    self.progress_changed.emit(completed, len(self.images))

            all_records = self.database.task_records(self.task_id)
            if self._cancelled.is_set():
                self.database.update_task_status(self.task_id, "已取消")
                self.batch_finished.emit(False, "任务已取消，已保留完成记录")
            else:
                self.database.update_task_status(self.task_id, "待复核" if any(r.status == RecordStatus.REVIEW for r in all_records) else "已完成")
                self.batch_finished.emit(True, "识别完成")
        except Exception as exc:
            self.database.update_task_status(self.task_id, "失败")
            self.batch_error.emit(str(exc))
