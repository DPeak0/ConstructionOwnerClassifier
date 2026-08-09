from __future__ import annotations

import threading
import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .database import Database
from .engine import RecognitionEngine
from .models import AppSettings, ClassificationRecord, RecordStatus
from .ocr import create_local_provider
from .performance import effective_concurrency
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
        self._ai_semaphore = threading.Semaphore(max(1, min(settings.ai_max_concurrency, 2)))
        self._owners_lock = threading.Lock()
        self._owners_version = 0

    def update_owners(self, owners: dict[str, list[str]]) -> None:
        snapshot = {
            owner: list(aliases) for owner, aliases in owners.items()
        }
        with self._owners_lock:
            self.owners = snapshot
            self._owners_version += 1

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._resume.set()

    def _engine(self) -> RecognitionEngine:
        engine = getattr(self._thread_local, "engine", None)
        with self._owners_lock:
            owners = {
                owner: list(aliases) for owner, aliases in self.owners.items()
            }
            owners_version = self._owners_version
        if engine is not None and getattr(self._thread_local, "owners_version", -1) != owners_version:
            engine.update_owners(owners)
            self._thread_local.owners_version = owners_version
        if engine is None:
            reviewer = None
            if self.settings.ai_enabled:
                try:
                    from .ai import GlmVisionReviewer
                    from .credentials import load_api_key

                    api_key = load_api_key()
                    if api_key:
                        reviewer = GlmVisionReviewer(
                            api_key, self.settings.ai_model,
                            self.settings.ai_timeout_seconds, self._ai_semaphore,
                        )
                except Exception:
                    reviewer = None
            local_workers = effective_concurrency(self.settings)
            inference_threads = max(
                1, min(4, (os.cpu_count() or 1) // max(local_workers, 1))
            )
            engine = RecognitionEngine(
                create_local_provider(
                    prefer_paddle=False, inference_threads=inference_threads,
                ),
                owners,
                self.settings,
                reviewer,
            )
            self._thread_local.engine = engine
            self._thread_local.owners_version = owners_version
        return engine

    def _recognize(self, path: Path) -> ClassificationRecord:
        return self._engine().classify(path)

    def _wait_until_resumed(self) -> bool:
        while not self._cancelled.is_set():
            if self._resume.wait(timeout=0.1):
                return not self._cancelled.is_set()
        return False

    def run(self) -> None:
        service = ClassificationService(self.output_root, self.settings)
        database: Database | None = None
        completed = 0
        futures: dict[Future[ClassificationRecord], int] = {}
        try:
            database = Database(self.database.path)
            concurrency = effective_concurrency(self.settings)
            next_index = 0

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                while futures or next_index < len(self.images):
                    if not self._wait_until_resumed():
                        for pending in futures:
                            pending.cancel()
                        break

                    while len(futures) < concurrency and next_index < len(self.images):
                        index = next_index
                        next_index += 1
                        futures[executor.submit(self._recognize, self.images[index])] = index

                    done, _pending = wait(
                        tuple(futures), timeout=0.1, return_when=FIRST_COMPLETED
                    )
                    if not done:
                        continue
                    if not self._wait_until_resumed():
                        for pending in futures:
                            pending.cancel()
                        break

                    for future in done:
                        index = futures.pop(future)
                        try:
                            record = future.result()
                            record.task_id = self.task_id
                            if record.status == RecordStatus.CONFIRMED:
                                service.classify(record, record.owner)
                            elif record.status == RecordStatus.UNRECOGNIZED:
                                service.classify(record, "未识别")
                            elif record.status == RecordStatus.NO_WATERMARK:
                                service.classify(record, "无水印")
                            database.save_record(record)
                            self.record_ready.emit(record, index)
                        except Exception as exc:
                            record = ClassificationRecord(
                                source_path=str(self.images[index]), task_id=self.task_id,
                                status=RecordStatus.FAILED, error=str(exc),
                            )
                            database.save_record(record)
                            self.record_ready.emit(record, index)
                        completed += 1
                        self.progress_changed.emit(completed, len(self.images))

            all_records = database.task_records(self.task_id)
            if self._cancelled.is_set():
                database.update_task_status(self.task_id, "已取消")
                self.batch_finished.emit(False, "任务已取消，已保留完成记录")
            else:
                pending_statuses = {
                    RecordStatus.REVIEW, RecordStatus.UNRECOGNIZED, RecordStatus.FAILED,
                }
                database.update_task_status(
                    self.task_id,
                    "待复核" if any(r.status in pending_statuses for r in all_records) else "已完成",
                )
                self.batch_finished.emit(True, "识别完成")
        except Exception as exc:
            if database is not None:
                try:
                    database.update_task_status(self.task_id, "失败")
                except Exception:
                    pass
            self.batch_error.emit(str(exc))
        finally:
            if database is not None:
                database.close()
