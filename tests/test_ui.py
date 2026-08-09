from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QMessageBox, QPushButton
from PIL import Image

from owner_classifier.app import ImagePreview, MainWindow, native_path_text
from owner_classifier.database import Database
from owner_classifier.models import ClassificationRecord, RecordStatus
from owner_classifier.services import ClassificationService


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
    assert window.table.columnCount() == 3
    assert [window.table.horizontalHeaderItem(i).text() for i in range(3)] == [
        "文件名", "原始路径", "状态",
    ]
    assert not hasattr(window, "review_table")
    assert not hasattr(window, "ocr_text")
    assert window.settings_dialog.isModal()
    assert window.settings_page.tabs.count() == 4
    assert window.settings_page.tabs.tabText(3) == "AI增强"
    assert [window.settings_page.tabs.tabText(i) for i in range(3)] == ["责任人名单", "识别设置", "软件更新"]
    assert not hasattr(window, "export_button")
    assert not hasattr(window.settings_page, "save_button")
    assert window.table.width() > 300
    assert abs(window.preview.width() - window.table.width()) < 80
    table_columns = sum(window.table.columnWidth(index) for index in range(3))
    assert abs(table_columns - window.table.viewport().width()) < 4
    assert window.settings_button.geometry().bottom() < window.input_edit.geometry().top()
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
    assert window.input_sources == []
    assert not window.scan_button.isEnabled()
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


class FakeUpdateWorker(QObject):
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.running = True
        self.interrupted = False

    def isRunning(self) -> bool:
        return self.running

    def cancel(self) -> None:
        self.interrupted = True


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


def test_close_waits_for_update_worker_to_stop(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    update_worker = FakeUpdateWorker()
    window.settings_page.update_check_worker = update_worker
    window.show()
    app.processEvents()

    assert window.close() is False
    assert update_worker.interrupted is True
    assert window.isVisible()
    assert window._database_closed is False

    update_worker.running = False
    update_worker.finished.emit()
    app.processEvents()
    app.processEvents()

    assert not window.isVisible()
    assert window._database_closed is True


def test_close_stops_recognition_and_update_workers_together(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    batch_worker = FakeRunningWorker()
    update_worker = FakeUpdateWorker()
    window.worker = batch_worker
    window.settings_page.update_check_worker = update_worker
    monkeypatch.setattr(window, "_confirm_cancel_and_close", lambda: True)
    window.show()
    app.processEvents()

    assert window.close() is False
    assert batch_worker.cancelled is True
    assert update_worker.interrupted is True

    batch_worker.running = False
    batch_worker.finished.emit()
    app.processEvents()
    assert window.isVisible()

    update_worker.running = False
    update_worker.finished.emit()
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
    assert not window.issue_banner.isHidden()
    assert "名单内候选接近" in window.issue_banner.text()
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

    page.new_owner.setText("张三, 李四，王五")
    page._add_owner()
    app.processEvents()
    assert not hasattr(page, "new_aliases")
    assert {"张三", "李四", "王五"}.issubset(set(window.database.owners()))
    assert window.database.owner_alias_map()["张三"] == []
    combo_owners = [window.owner_combo.itemText(i) for i in range(window.owner_combo.count())]
    assert {"张三", "李四", "王五"}.issubset(set(combo_owners))

    owner_id = next(row[0] for row in window.database.owner_rows() if row[1] == "张三")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    page._delete_owner(owner_id)
    app.processEvents()
    assert "张三" not in window.database.owners()
    assert "张三" not in [window.owner_combo.itemText(i) for i in range(window.owner_combo.count())]
    assert window.owner_combo.maxVisibleItems() == 12
    assert window.owner_combo.view().verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert window.owner_combo.maximumWidth() == 320
    assert page.owner_table.columnWidth(3) >= 138
    window.settings_dialog.show()
    page.tabs.setCurrentIndex(0)
    app.processEvents()
    assert page.new_owner.geometry().bottom() < page.owner_table.geometry().top()
    assert page.auto_threshold.buttonSymbols() == QAbstractSpinBox.NoButtons
    assert page.review_threshold.buttonSymbols() == QAbstractSpinBox.NoButtons
    assert page.concurrency.currentData() == 0
    assert "自动（推荐" in page.concurrency.currentText()
    assert page.auto_threshold.maximumWidth() == 300
    assert page.recognition_keywords.width() > page.auto_threshold.width()
    window.close()


def test_owner_edit_uses_chinese_buttons_and_keeps_alias_editing():
    from owner_classifier.dialogs import OwnerEditDialog

    app = application()
    dialog = OwnerEditDialog("修改责任人", "曹华兵", ["曹华斌"])
    assert dialog.confirm_button.text() == "确定"
    assert dialog.cancel_button.text() == "取消"
    assert dialog.values() == ("曹华兵", ["曹华斌"])
    assert {button.text() for button in dialog.findChildren(QPushButton)} >= {"确定", "取消"}
    dialog.close()


def test_settings_dialog_has_no_bottom_close_button(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    direct_buttons = window.settings_dialog.findChildren(QPushButton, options=Qt.FindDirectChildrenOnly)
    assert not any(button.text() in {"Close", "关闭"} for button in direct_buttons)
    page = window.settings_page
    page.reload()
    page.reload()
    app.processEvents()
    owner_actions = page.owner_table.cellWidget(0, 3)
    assert owner_actions is not None
    buttons = owner_actions.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["修改", "删除"]
    assert sum(button.width() for button in buttons) + 16 <= page.owner_table.columnWidth(3)
    window.close()


def test_mixed_input_sources_scan_and_search_suggestions(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    folder = tmp_path / "folder"
    folder.mkdir()
    first = folder / "施工现场一.jpg"
    second = tmp_path / "施工现场二.png"
    first.write_bytes(b"jpg")
    second.write_bytes(b"png")

    window = MainWindow()
    assert not window.scan_button.isEnabled()
    window._add_input_sources([str(folder), str(second), str(first)])
    assert window.scan_button.isEnabled()
    assert len(window.input_sources) == 3

    window._scan()
    app.processEvents()

    assert {path.name for path in window.images} == {"施工现场一.jpg", "施工现场二.png"}
    assert window.table.rowCount() == 2
    assert window.table.item(0, 1).text()
    assert window.table.columnWidth(2) >= 88
    suggestions = set(window.search_completion_model.stringList())
    assert suggestions == {"施工现场一.jpg", "施工现场二.png"}
    assert window.search_completer.filterMode() == Qt.MatchContains
    assert window.owner_completer.filterMode() == Qt.MatchContains
    window.close()


def test_combo_popup_is_anchored_outside_editor(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    window.settings_dialog.show()
    window.settings_page.tabs.setCurrentIndex(1)
    app.processEvents()

    combo = window.settings_page.duplicate_policy
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()
    editor_bottom = combo.mapToGlobal(QPoint(0, combo.height())).y()
    assert popup.geometry().top() >= editor_bottom
    combo.hidePopup()
    window.close()


def test_confirm_classification_button_has_no_icon(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    window = MainWindow()
    assert window.confirm_button.icon().isNull()
    window.close()


def test_classified_record_can_be_manually_reclassified(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    output = tmp_path / "output"
    output.mkdir()

    window = MainWindow()
    window.current_task_id = window.database.create_task(str(tmp_path), str(output))
    window.session_output = output
    record = ClassificationService(output, window.database.load_settings()).classify(
        ClassificationRecord(str(source), task_id=window.current_task_id), "刘纪林"
    )
    window.database.save_record(record)
    window.records = [record]
    old_output = Path(record.output_path)
    window._populate_task_table()
    window.table.selectRow(0)
    app.processEvents()

    assert window.confirm_button.isEnabled()
    assert window.confirm_button.text() == "重新分类"
    window.owner_combo.setCurrentText("吴万松")
    window._confirm_review()

    assert window.records[0].owner == "吴万松"
    assert Path(window.records[0].output_path).parent.name == "吴万松"
    assert not old_output.exists()
    window.close()


def test_scan_without_sources_does_not_use_working_directory(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    (tmp_path / "unexpected.jpg").write_bytes(b"jpg")
    monkeypatch.chdir(tmp_path)
    window = MainWindow()

    window._scan()

    assert window.images == []
    assert window.records == []
    assert not window.scan_button.isEnabled()
    assert "请先选择" in window.status_label.text()
    window.close()


def test_candidate_buttons_are_sorted_and_select_owner(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    image_path = tmp_path / "candidate.png"
    Image.new("RGB", (80, 60), "white").save(image_path)
    output = tmp_path / "output"
    output.mkdir()

    window = MainWindow()
    task_id = window.database.create_task(str(tmp_path), str(output))
    window.current_task_id = task_id
    window.session_output = output
    window.records = [ClassificationRecord(
        str(image_path), candidate_owner="刘纪林", confidence=0.6,
        candidate_owners=[("刘纪林", 0.6), ("吴万松", 0.9), ("曹华兵", 0.7)],
        status=RecordStatus.REVIEW, task_id=task_id,
    )]
    window._populate_task_table()
    window.table.selectRow(0)
    app.processEvents()

    assert [button.text() for button in window.candidate_buttons] == [
        "吴万松  90%", "曹华兵  70%", "刘纪林  60%"
    ]
    window.candidate_buttons[0].click()
    assert window.owner_combo.currentText() == "吴万松"
    window.close()


def test_name_spelling_ambiguity_shows_specific_issue_and_both_choices(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    image_path = tmp_path / "ambiguous.png"
    Image.new("RGB", (80, 60), "white").save(image_path)

    window = MainWindow()
    record = ClassificationRecord(
        str(image_path), candidate_owners=[("曹华斌", 0.99), ("曹华兵", 0.84)],
        confidence=0.99, watermark_score=0.98, status=RecordStatus.REVIEW,
        decision_source="name_spelling_ambiguity",
    )
    window.records = [record]
    window._populate_task_table()
    window.table.selectRow(0)
    app.processEvents()

    assert "仅一字不同" in window.issue_banner.text()
    assert {button.toolTip().split("（", 1)[0].removeprefix("选择候选责任人 ") for button in window.candidate_buttons} == {"曹华兵", "曹华斌"}
    window.close()


def test_name_spelling_ambiguity_reconciles_only_after_observed_name_is_added(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    application()
    window = MainWindow()
    record = ClassificationRecord(
        str(tmp_path / "ambiguous.png"),
        candidate_owners=[("曹华斌", 0.99), ("曹华兵", 0.84)],
        confidence=0.99, watermark_score=0.98, status=RecordStatus.REVIEW,
        decision_source="name_spelling_ambiguity",
    )

    assert window._matching_current_owner(record) is None
    window.database.add_owner("曹华斌")
    match = window._matching_current_owner(record)
    assert match is not None
    assert match[0] == "曹华斌"
    window.close()


def test_candidate_buttons_wrap_without_overlap_at_minimum_width(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    image_path = tmp_path / "candidate.png"
    Image.new("RGB", (80, 60), "white").save(image_path)

    window = MainWindow()
    window.resize(1120, 700)
    window.current_task_id = 1
    window.session_output = tmp_path / "output"
    window.records = [ClassificationRecord(
        str(image_path), confidence=0.9,
        candidate_owners=[
            ("候选责任人甲", 0.90), ("候选责任人乙", 0.85),
            ("候选责任人丙", 0.80), ("候选责任人丁", 0.75),
            ("候选责任人戊", 0.70),
        ],
        status=RecordStatus.REVIEW, task_id=1,
    )]
    window._populate_task_table()
    window.table.selectRow(0)
    window.show()
    app.processEvents()

    geometries = [
        button.geometry().translated(
            button.parentWidget().mapTo(window.candidate_section, QPoint(0, 0))
        )
        for button in window.candidate_buttons
    ]
    assert len({geometry.y() for geometry in geometries}) > 1
    assert all(window.candidate_section.rect().contains(geometry) for geometry in geometries)
    assert all(
        not first.intersects(second)
        for index, first in enumerate(geometries)
        for second in geometries[index + 1:]
    )
    candidate_bottom = max(
        button.mapTo(window, QPoint(0, button.height())).y()
        for button in window.candidate_buttons
    )
    owner_top = window.owner_combo.mapTo(window, QPoint(0, 0)).y()
    assert candidate_bottom < owner_top
    window.close()


def test_unknown_owner_can_be_classified_without_adding_to_list(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    image_path = tmp_path / "unknown.png"
    Image.new("RGB", (80, 60), "white").save(image_path)
    output = tmp_path / "output"
    output.mkdir()

    window = MainWindow()
    task_id = window.database.create_task(str(tmp_path), str(output))
    window.current_task_id = task_id
    window.session_output = output
    record = ClassificationRecord(
        str(image_path), confidence=0.78,
        candidate_owners=[("名单外责任人", 0.78)],
        status=RecordStatus.UNRECOGNIZED, task_id=task_id,
    )
    window.records = [record]
    window._populate_task_table()
    window.table.selectRow(0)
    app.processEvents()
    assert window.candidate_value.text() == "名单外责任人"
    assert not window.issue_banner.isHidden()
    assert "未找到可信" in window.issue_banner.text()
    assert window.owner_combo.currentText() == "名单外责任人"
    assert window.confirm_button.isEnabled()
    window._ask_unknown_owner_decision = lambda _owner: "classify"
    window._confirm_review()

    assert record.status == RecordStatus.CLASSIFIED
    assert Path(record.output_path).parent.name == "名单外责任人"
    assert "名单外责任人" not in window.database.owners()
    window.close()


def test_adding_first_unknown_owner_auto_classifies_later_matching_records(
    monkeypatch, tmp_path: Path,
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    app = application()
    output = tmp_path / "output"
    output.mkdir()
    paths = [tmp_path / f"owner-{index}.png" for index in range(3)]
    for path in paths:
        Image.new("RGB", (80, 60), "white").save(path)

    window = MainWindow()
    task_id = window.database.create_task(str(tmp_path), str(output))
    window.current_task_id = task_id
    window.session_output = output
    window.records = [
        ClassificationRecord(
            str(path), confidence=0.96, local_confidence=0.96,
            candidate_owners=[("张三", 0.96)], watermark_score=0.95,
            status=RecordStatus.REVIEW, task_id=task_id,
        )
        for path in paths
    ]
    for record in window.records:
        window.database.save_record(record)
    window._populate_task_table()
    window.table.selectRow(0)
    app.processEvents()
    window._ask_unknown_owner_decision = lambda _owner: "add"

    window._confirm_review()

    assert "张三" in window.database.owners()
    assert all(record.status == RecordStatus.CLASSIFIED for record in window.records)
    assert window.records[0].reviewed
    assert all(not record.reviewed for record in window.records[1:])
    assert all(
        record.decision_source == "owner_list_refresh"
        for record in window.records[1:]
    )
    assert len(list((output / "张三").glob("*.png"))) == 3
    assert "自动分类 2 个同名任务" in window.status_label.text()
    window.close()


def test_later_worker_result_rechecks_current_owner_list(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    application()
    path = tmp_path / "later.png"
    Image.new("RGB", (80, 60), "white").save(path)
    output = tmp_path / "output"
    output.mkdir()

    window = MainWindow()
    window.database.add_owner("张三")
    task_id = window.database.create_task(str(tmp_path), str(output))
    window.current_task_id = task_id
    window.session_output = output
    window.records = [ClassificationRecord(str(path), task_id=task_id)]
    window._populate_task_table()
    incoming = ClassificationRecord(
        str(path), confidence=0.97, local_confidence=0.97,
        candidate_owners=[("张三", 0.97)], watermark_score=0.96,
        status=RecordStatus.REVIEW, task_id=task_id,
    )

    window._record_ready(incoming, 0)

    assert incoming.status == RecordStatus.CLASSIFIED
    assert incoming.owner == "张三"
    assert incoming.decision_source == "owner_list_refresh"
    assert Path(incoming.output_path).parent.name == "张三"
    window.close()
