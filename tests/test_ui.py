from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from PIL import Image

from owner_classifier.app import ImagePreview, MainWindow
from owner_classifier.models import ClassificationRecord, RecordStatus


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_main_window_has_only_task_review_and_settings(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    app.processEvents()

    assert window.pages.count() == 3
    assert [button.text().split()[0] for button in window.nav_buttons] == ["分类任务", "待复核", "设置"]
    assert window.settings_page.tabs.count() == 3
    assert [window.settings_page.tabs.tabText(i) for i in range(3)] == ["责任人名单", "识别设置", "软件更新"]
    assert not hasattr(window, "export_button")
    assert not hasattr(window.settings_page, "save_button")
    assert window.table.width() > 700
    window.close()


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

    window._confirm_review()
    app.processEvents()

    assert window._selected_review_index() == 1
    assert window.preview.current_source == str(second_path)
    assert window.review_detail.text().startswith("second.png")
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
