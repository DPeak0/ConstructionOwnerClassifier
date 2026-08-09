from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from PIL import Image

from owner_classifier.app import ImagePreview, MainWindow, native_path_text
from owner_classifier.database import Database
from owner_classifier.models import ClassificationRecord, RecordStatus


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_main_window_uses_single_page_dashboard_and_settings_dialog(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    assert not hasattr(window, "pages")
    assert not hasattr(window, "nav_buttons")
    assert window.table.columnCount() == 2
    assert [window.table.horizontalHeaderItem(i).text() for i in range(2)] == ["文件名", "状态"]
    assert not hasattr(window, "review_table")
    assert not hasattr(window, "ocr_text")
    assert window.settings_dialog.isModal()
    assert window.settings_page.tabs.count() == 3
    assert [window.settings_page.tabs.tabText(i) for i in range(3)] == ["责任人名单", "识别设置", "软件更新"]
    assert not hasattr(window, "export_button")
    assert not hasattr(window.settings_page, "save_button")
    assert window.table.width() > 300
    assert window.preview.width() > window.table.width()
    window.close()


def test_main_window_starts_with_empty_task_even_when_database_has_records(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    database = Database()
    task_id = database.create_task("C:/previous/input", "C:/previous/output/session")
    database.save_record(ClassificationRecord("C:/previous/input/photo.jpg", task_id=task_id))
    database.close()

    app = application()
    window = MainWindow()
    app.processEvents()

    assert window.input_edit.text() == ""
    assert window.output_edit.text() == ""
    assert window.records == []
    assert window.table.rowCount() == 0
    assert window.current_task_id is None
    window.close()


def test_native_path_text_uses_platform_separators():
    result = native_path_text("C:/work/photos")
    assert result == os.path.normpath("C:/work/photos")
    if os.name == "nt":
        assert result == r"C:\work\photos"
        assert "/" not in result


class FakeRunningWorker(QObject):
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.running = True
        self.cancelled = False

    def isRunning(self) -> bool:
        return self.running

    def cancel(self) -> None:
        self.cancelled = True


def test_close_during_recognition_can_continue(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    worker = FakeRunningWorker()
    window.worker = worker
    monkeypatch.setattr(window, "_confirm_cancel_and_close", lambda: False)
    window.show()
    app.processEvents()

    assert window.close() is False
    assert worker.cancelled is False
    assert window.isVisible()

    worker.running = False
    window.close()


def test_close_during_recognition_waits_for_safe_worker_stop(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    worker = FakeRunningWorker()
    window.worker = worker
    monkeypatch.setattr(window, "_confirm_cancel_and_close", lambda: True)
    window.show()
    app.processEvents()

    assert window.close() is False
    assert worker.cancelled is True
    assert window.isVisible()
    assert window._database_closed is False
    assert "安全中断" in window.status_label.text()

    worker.running = False
    worker.finished.emit()
    app.processEvents()
    app.processEvents()
    assert not window.isVisible()
    assert window._database_closed is True


def test_review_confirmation_selects_and_displays_next_image(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    Image.new("RGB", (80, 60), "red").save(first_path)
    Image.new("RGB", (80, 60), "blue").save(second_path)
    output = tmp_path / "output"
    output.mkdir()

    window = MainWindow()
    task_id = window.database.create_task(str(tmp_path), str(output))
    window.current_task_id = task_id
    window.session_output = output
    window.records = [
        ClassificationRecord(
            str(first_path), candidate_owner="刘纪林", confidence=0.70,
            status=RecordStatus.REVIEW, task_id=task_id,
        ),
        ClassificationRecord(
            str(second_path), candidate_owner="吴万松", confidence=0.71,
            status=RecordStatus.REVIEW, task_id=task_id,
        ),
    ]
    for record in window.records:
        window.database.save_record(record)
    window._populate_task_table()
    window._refresh_review_table()
    assert window.preview.current_source == str(first_path)

    window.table.setFocus()
    QTest.keyClick(window.table, Qt.Key_Return)
    app.processEvents()

    assert window._selected_review_index() == 1
    assert window.preview.current_source == str(second_path)
    assert window.selected_file_label.text() == "second.png"
    window.close()


def test_dashboard_keyboard_navigation_rotation_and_filter(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    Image.new("RGB", (120, 80), "red").save(first_path)
    Image.new("RGB", (80, 120), "blue").save(second_path)

    window = MainWindow()
    window.records = [
        ClassificationRecord(str(first_path), status=RecordStatus.CLASSIFIED),
        ClassificationRecord(str(second_path), status=RecordStatus.REVIEW),
    ]
    window._populate_task_table()
    window.table.selectRow(0)
    window.table.setFocus()
    window.show()
    app.processEvents()

    QTest.keyClick(window.table, Qt.Key_Down)
    app.processEvents()
    assert window._selected_record_index() == 1
    assert window.preview.current_source == str(second_path)

    window.preview.setFocus()
    QTest.keyClick(window.preview, Qt.Key_R)
    app.processEvents()
    assert window.preview.view_rotation == 90

    window.status_filter.setCurrentIndex(window.status_filter.findData("classified"))
    app.processEvents()
    assert not window.table.isRowHidden(0)
    assert window.table.isRowHidden(1)
    window.close()


def test_review_remains_available_while_batch_is_running(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    image_path = tmp_path / "review.png"
    Image.new("RGB", (80, 60), "white").save(image_path)
    output = tmp_path / "output"
    output.mkdir()

    window = MainWindow()
    window.current_task_id = window.database.create_task(str(tmp_path), str(output))
    window.session_output = output
    window.records = [
        ClassificationRecord(
            str(image_path), candidate_owner="刘纪林", confidence=0.7,
            status=RecordStatus.REVIEW, task_id=window.current_task_id,
        )
    ]
    window.worker = FakeRunningWorker()
    window._populate_task_table()
    window.table.selectRow(0)
    app.processEvents()

    assert window.confirm_button.isEnabled()
    assert window.owner_combo.isEnabled()

    window.worker.running = False
    window.close()


def test_image_preview_mouse_anchored_zoom_and_pan(tmp_path: Path):
    app = application()
    image_path = tmp_path / "zoom.png"
    Image.new("RGB", (1200, 900), "white").save(image_path)
    preview = ImagePreview()
    preview.resize(500, 400)
    preview.show()
    app.processEvents()
    preview.set_record(ClassificationRecord(str(image_path)))
    fitted = preview.scale_factor
    assert 0 < fitted < 1

    preview._wheel_zoom(1, QPointF(250, 200), QPointF(250, 200))
    assert preview.scale_factor > fitted
    wheel_scale = preview.scale_factor
    preview._double_click_zoom(QPointF(250, 200), QPointF(250, 200))
    assert preview.scale_factor == wheel_scale * 2

    preview.actual_size()
    preview.zoom_in()
    preview.scroll.horizontalScrollBar().setValue(100)
    preview.scroll.verticalScrollBar().setValue(100)
    preview._pan(QPoint(20, 15))
    assert preview.scroll.horizontalScrollBar().value() == 80
    assert preview.scroll.verticalScrollBar().value() == 85
    preview.close()


def test_settings_auto_save_and_owner_changes_apply_immediately(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    page = window.settings_page

    page.file_operation.setCurrentText("移动")
    page.recognition_keywords.setPlainText("现场负责人\n责任人")
    QTest.qWait(450)
    settings = window.database.load_settings()
    assert settings.file_operation == "移动"
    assert settings.recognition_keywords == ["现场负责人", "责任人"]

    page.new_owner.setText("张三")
    page.new_aliases.setText("张叁;老张")
    page._add_owner()
    app.processEvents()
    assert window.database.owner_alias_map()["张三"] == ["张叁", "老张"]
    assert "张三" in [window.owner_combo.itemText(i) for i in range(window.owner_combo.count())]

    owner_id = next(row[0] for row in window.database.owner_rows() if row[1] == "张三")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page._delete_owner(owner_id)
    app.processEvents()
    assert "张三" not in window.database.owners()
    assert "张三" not in [window.owner_combo.itemText(i) for i in range(window.owner_combo.count())]
    assert window.owner_combo.maxVisibleItems() == 12
    assert window.owner_combo.view().verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    window.close()
