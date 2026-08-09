from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QStyle, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__
from .database import Database
from .dialogs import SettingsDialog
from .models import ClassificationRecord, RecordStatus
from .preview import ImagePreview
from .scanner import scan_images
from .services import ClassificationService
from .worker import BatchWorker


APP_NAME = "施工责任人图片分类器"
ACTIONABLE_STATUSES = {
    RecordStatus.REVIEW,
    RecordStatus.UNRECOGNIZED,
    RecordStatus.FAILED,
}


def native_path_text(value: str | Path) -> str:
    return os.path.normpath(str(value))


class TaskTable(QTableWidget):
    confirm_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
            self.confirm_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.database = Database()
        self.settings = self.database.load_settings()
        self.images: list[Path] = []
        self.records: list[ClassificationRecord] = []
        self.current_task_id: int | None = None
        self.session_output: Path | None = None
        self.worker: BatchWorker | None = None
        self.paused = False
        self._close_after_worker = False
        self._database_closed = False

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 880)
        self.setMinimumSize(1120, 700)
        self.setCentralWidget(self._build_dashboard())

        self.settings_dialog = SettingsDialog(self.database, self)
        self.settings_page = self.settings_dialog.page
        self.settings_page.settings_saved.connect(self._settings_saved)
        self.settings_page.owners_changed.connect(self._owners_changed)
        self.settings_page.update_available.connect(self._update_available)

        self.rotate_shortcut = QShortcut(QKeySequence("R"), self.preview)
        self.rotate_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.rotate_shortcut.activated.connect(self.preview.rotate_clockwise)
        self._apply_style()

        if self.settings.update_auto_check and getattr(sys, "frozen", False):
            QTimer.singleShot(2500, lambda: self.settings_page.check_for_updates(silent=True))

    def _icon(self, standard: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(standard)

    def _command_button(
        self,
        text: str,
        icon: QStyle.StandardPixmap,
        slot,
        primary: bool = False,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(self._icon(icon))
        button.setMinimumHeight(34)
        if primary:
            button.setObjectName("primaryButton")
        button.clicked.connect(slot)
        return button

    def _icon_button(
        self, icon: QStyle.StandardPixmap, tooltip: str, slot
    ) -> QToolButton:
        button = QToolButton()
        button.setIcon(self._icon(icon))
        button.setToolTip(tooltip)
        button.setFixedSize(34, 34)
        button.clicked.connect(slot)
        return button

    def _build_dashboard(self) -> QWidget:
        root = QWidget()
        root.setObjectName("dashboard")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(10)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_stats())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("workspaceSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_task_list())
        splitter.addWidget(self._build_inspector())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([500, 820])
        layout.addWidget(splitter, 1)
        layout.addWidget(self._build_footer())
        return root

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("headerBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel(APP_NAME)
        title.setObjectName("brandTitle")
        version = QLabel(f"版本 {__version__}")
        version.setObjectName("versionText")
        brand.addWidget(title)
        brand.addWidget(version)
        layout.addLayout(brand)

        input_box = QVBoxLayout()
        input_box.setSpacing(3)
        input_box.addWidget(self._field_label("输入目录"))
        input_row = QHBoxLayout()
        input_row.setSpacing(4)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择施工照片目录")
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(
            self._icon_button(QStyle.SP_DirOpenIcon, "选择输入目录", self._choose_input)
        )
        input_box.addLayout(input_row)
        layout.addLayout(input_box, 3)

        output_box = QVBoxLayout()
        output_box.setSpacing(3)
        output_box.addWidget(self._field_label("保存位置"))
        output_row = QHBoxLayout()
        output_row.setSpacing(4)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择分类结果保存位置")
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(
            self._icon_button(QStyle.SP_DirOpenIcon, "选择保存位置", self._choose_output)
        )
        self.open_button = self._icon_button(
            QStyle.SP_DialogOpenButton, "打开本次分类结果", self._open_output
        )
        self.open_button.setEnabled(False)
        output_row.addWidget(self.open_button)
        output_box.addLayout(output_row)
        layout.addLayout(output_box, 3)

        self.scan_button = self._command_button(
            "扫描", QStyle.SP_FileDialogContentsView, self._scan
        )
        self.start_button = self._command_button(
            "开始识别", QStyle.SP_MediaPlay, self._primary_action, True
        )
        self.cancel_button = self._command_button(
            "取消", QStyle.SP_BrowserStop, self._cancel
        )
        self.settings_button = self._command_button(
            "设置", QStyle.SP_FileDialogDetailedView, self._show_settings
        )
        for button in (
            self.scan_button,
            self.start_button,
            self.cancel_button,
            self.settings_button,
        ):
            layout.addWidget(button, 0, Qt.AlignBottom)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        return header

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _build_stats(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("statsBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stat_total = self._stat_label("总数", "statTotal")
        self.stat_classified = self._stat_label("高置信度", "statClassified")
        self.stat_review = self._stat_label("待复核", "statReview")
        self.stat_failed = self._stat_label("异常 / 未识别", "statFailed")
        for label in (
            self.stat_total,
            self.stat_classified,
            self.stat_review,
            self.stat_failed,
        ):
            layout.addWidget(label, 1)
        return bar

    @staticmethod
    def _stat_label(title: str, name: str) -> QLabel:
        label = QLabel(f"{title}  0")
        label.setObjectName(name)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumHeight(42)
        return label

    def _build_task_list(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("listPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(8)

        heading = QLabel("任务列表")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文件名")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.status_filter = QComboBox()
        self.status_filter.addItem("全部状态", "all")
        self.status_filter.addItem("仅待复核", "review")
        self.status_filter.addItem("已分类", "classified")
        self.status_filter.addItem("异常 / 未识别", "abnormal")
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.status_filter)
        layout.addLayout(filters)

        self.table = TaskTable(0, 2)
        self.table.setHorizontalHeaderLabels(["文件名", "状态"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_selected_record)
        self.table.confirm_requested.connect(self._confirm_review)
        layout.addWidget(self.table, 1)
        return panel

    def _build_inspector(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("inspectorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(8)

        self.selected_file_label = QLabel("大图预览")
        self.selected_file_label.setObjectName("sectionTitle")
        self.selected_file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.selected_file_label)
        self.preview = ImagePreview()
        layout.addWidget(self.preview, 1)

        review = QFrame()
        review.setObjectName("reviewControls")
        review_layout = QVBoxLayout(review)
        review_layout.setContentsMargins(14, 12, 14, 12)
        review_layout.setSpacing(8)

        result_row = QHBoxLayout()
        result_row.setSpacing(8)
        result_row.addWidget(QLabel("识别责任人"))
        self.candidate_value = QLabel("—")
        self.candidate_value.setObjectName("candidateValue")
        result_row.addWidget(self.candidate_value)
        result_row.addSpacing(14)
        self.confidence_value = QLabel("置信度 —")
        self.confidence_value.setObjectName("confidenceBadge")
        result_row.addWidget(self.confidence_value)
        self.record_status_value = QLabel("等待选择")
        self.record_status_value.setObjectName("recordStatus")
        result_row.addWidget(self.record_status_value)
        result_row.addStretch(1)
        review_layout.addLayout(result_row)

        self.record_error = QLabel()
        self.record_error.setObjectName("errorText")
        self.record_error.setWordWrap(True)
        self.record_error.hide()
        review_layout.addWidget(self.record_error)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(QLabel("人工复核修正"))
        self.owner_combo = QComboBox()
        self.owner_combo.setEditable(True)
        self.owner_combo.setInsertPolicy(QComboBox.NoInsert)
        self.owner_combo.setMaxVisibleItems(12)
        self.owner_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.owner_combo.setMinimumHeight(36)
        self.owner_completer = QCompleter(self.owner_combo.model(), self.owner_combo)
        self.owner_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.owner_completer.setFilterMode(Qt.MatchContains)
        self.owner_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.owner_combo.setCompleter(self.owner_completer)
        self.owner_combo.lineEdit().returnPressed.connect(self._confirm_review)
        self.owner_combo.addItems(self.database.owners())
        action_row.addWidget(self.owner_combo, 1)
        self.confirm_button = self._command_button(
            "确认分类", QStyle.SP_DialogApplyButton, self._confirm_review, True
        )
        self.confirm_button.setEnabled(False)
        action_row.addWidget(self.confirm_button)
        review_layout.addLayout(action_row)
        layout.addWidget(review)
        return panel

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("footerBar")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(4, 2, 4, 0)
        layout.setSpacing(10)
        self.status_label = QLabel("等待选择目录")
        self.status_label.setObjectName("secondaryText")
        self.status_label.setMinimumWidth(210)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress, 1)
        return footer

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#dashboard {
                background:#f8fafc; color:#1f2937;
                font-family:"Microsoft YaHei UI"; font-size:13px;
            }
            QFrame#headerBar, QFrame#statsBar, QFrame#reviewControls {
                background:#ffffff; border:1px solid #dbe3ea; border-radius:6px;
            }
            QLabel#brandTitle { color:#10233d; font-size:17px; font-weight:700; }
            QLabel#versionText, QLabel#fieldLabel, QLabel#secondaryText { color:#64748b; }
            QLabel#fieldLabel { font-size:12px; }
            QLabel#sectionTitle { color:#172033; font-size:15px; font-weight:700; padding:2px 0; }
            QLabel#statTotal, QLabel#statClassified, QLabel#statReview, QLabel#statFailed {
                background:#ffffff; border:0; border-right:1px solid #e5eaf0;
                color:#334155; font-weight:600;
            }
            QLabel#statClassified { color:#166534; }
            QLabel#statReview { color:#9a6700; }
            QLabel#statFailed { color:#b42318; border-right:0; }
            QLabel#candidateValue { color:#10233d; font-size:15px; font-weight:700; }
            QLabel#confidenceBadge, QLabel#recordStatus {
                background:#eef2f6; color:#475569; border-radius:4px; padding:4px 8px;
            }
            QLabel#errorText { color:#b42318; }
            QFrame#listPanel, QFrame#inspectorPanel, QFrame#footerBar { border:0; background:transparent; }
            QScrollArea#imagePreview, QLabel#imageCanvas {
                background:#111827; color:#aab5bd; border:0;
            }
            QScrollArea#imagePreview { border:1px solid #273244; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
                background:#ffffff; border:1px solid #bdc8d3; border-radius:4px;
                padding:6px; selection-background-color:#2563eb;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color:#2563eb; }
            QPushButton {
                background:#ffffff; border:1px solid #b8c4cf; border-radius:4px;
                padding:6px 11px;
            }
            QPushButton:hover, QToolButton:hover { background:#eff6ff; border-color:#2563eb; }
            QPushButton:disabled { color:#98a2ad; background:#edf0f3; border-color:#d8dee4; }
            QPushButton#primaryButton {
                background:#2563eb; color:#ffffff; border-color:#2563eb; font-weight:600;
            }
            QPushButton#primaryButton:hover { background:#1d4ed8; }
            QToolButton {
                background:#ffffff; border:1px solid #b8c4cf; border-radius:4px; padding:4px;
            }
            QHeaderView::section {
                background:#eef2f6; color:#475569; padding:8px; border:0;
                border-bottom:1px solid #dbe3ea; font-weight:600;
            }
            QTableWidget {
                background:#ffffff; alternate-background-color:#f8fafc;
                border:1px solid #d6dee6; gridline-color:transparent;
            }
            QTableWidget::item { padding:6px; border-bottom:1px solid #edf1f4; }
            QTableWidget::item:selected { background:#dbeafe; color:#172033; }
            QProgressBar {
                background:#e2e8f0; border:0; border-radius:3px; height:13px; text-align:center;
            }
            QProgressBar::chunk { background:#2f855a; border-radius:3px; }
            QSplitter::handle { background:#dbe3ea; width:1px; }
            QTabWidget::pane { background:#ffffff; border:1px solid #d6dee6; }
            QTabBar::tab { background:#eef2f6; padding:9px 16px; margin-right:1px; }
            QTabBar::tab:selected { background:#ffffff; color:#1d4ed8; font-weight:600; }
            """
        )

    def _choose_input(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择输入目录", self.input_edit.text()
        )
        if directory:
            self.input_edit.setText(native_path_text(directory))
            if not self.output_edit.text():
                self.output_edit.setText(native_path_text(Path(directory).parent))

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择结果保存位置", self.output_edit.text()
        )
        if directory:
            self.output_edit.setText(native_path_text(directory))

    def _scan(self) -> None:
        try:
            input_dir = Path(self.input_edit.text().strip()).expanduser().resolve()
            output_dir = (
                Path(self.output_edit.text().strip()).expanduser().resolve()
                if self.output_edit.text().strip()
                else input_dir.parent
            )
            self.input_edit.setText(native_path_text(input_dir))
            self.output_edit.setText(native_path_text(output_dir))
            self.images = scan_images(input_dir, output_dir)
            self.records = [ClassificationRecord(source_path=str(path)) for path in self.images]
            self.current_task_id = None
            self.session_output = None
            self._populate_task_table()
            self.start_button.setEnabled(bool(self.images))
            self.open_button.setEnabled(False)
            self.progress.setMaximum(max(len(self.images), 1))
            self.progress.setValue(0)
            self.status_label.setText("扫描完成" if self.images else "未找到支持的图片")
            self._update_counts()
            if self.records:
                self.table.selectRow(0)
                self.table.setFocus()
            else:
                self._show_selected_record()
        except Exception as exc:
            QMessageBox.warning(self, "扫描失败", str(exc))

    def _populate_task_table(self) -> None:
        selected = self._selected_record_index()
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            self._update_task_row(row, record)
        self._apply_filters()
        if selected is not None and selected < len(self.records):
            self._select_record_index(selected)

    def _status_display(self, status: RecordStatus) -> tuple[str, str, str]:
        values = {
            RecordStatus.PENDING: ("等待识别", "#eef2f6", "#475569"),
            RecordStatus.PROCESSING: ("识别中", "#dbeafe", "#1d4ed8"),
            RecordStatus.REVIEW: ("待复核", "#fef3c7", "#92400e"),
            RecordStatus.CONFIRMED: ("已确认", "#dcfce7", "#166534"),
            RecordStatus.CLASSIFIED: ("已分类", "#dcfce7", "#166534"),
            RecordStatus.UNRECOGNIZED: ("未识别", "#fee2e2", "#b42318"),
            RecordStatus.FAILED: ("异常", "#fee2e2", "#b42318"),
        }
        return values[status]

    def _update_task_row(
        self, row: int, record: ClassificationRecord, load_thumbnail: bool = True
    ) -> None:
        del load_thumbnail
        file_item = QTableWidgetItem(record.file_name)
        file_item.setData(Qt.UserRole, row)
        status_text, background, foreground = self._status_display(record.status)
        status_item = QTableWidgetItem(status_text)
        status_item.setTextAlignment(Qt.AlignCenter)
        status_item.setBackground(QColor(background))
        status_item.setForeground(QColor(foreground))
        self.table.setItem(row, 0, file_item)
        self.table.setItem(row, 1, status_item)
        if self._selected_record_index() == row:
            self._show_selected_record()

    def _matches_status_filter(self, record: ClassificationRecord) -> bool:
        mode = self.status_filter.currentData()
        if mode == "review":
            return record.status == RecordStatus.REVIEW
        if mode == "classified":
            return record.status in {RecordStatus.CLASSIFIED, RecordStatus.CONFIRMED}
        if mode == "abnormal":
            return record.status in {RecordStatus.UNRECOGNIZED, RecordStatus.FAILED}
        return True

    def _apply_filters(self, *_args) -> None:
        query = self.search_edit.text().strip().casefold()
        for row, record in enumerate(self.records):
            visible = (not query or query in record.file_name.casefold()) and self._matches_status_filter(record)
            self.table.setRowHidden(row, not visible)
        current = self.table.currentRow()
        if current >= 0 and self.table.isRowHidden(current):
            first_visible = next(
                (row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)),
                None,
            )
            if first_visible is None:
                self.table.clearSelection()
                self._show_selected_record()
            else:
                self.table.selectRow(first_visible)

    def _selected_record_index(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.UserRole)) if item is not None else None

    def _selected_review_index(self) -> int | None:
        return self._selected_record_index()

    def _select_record_index(self, index: int) -> None:
        if not 0 <= index < self.table.rowCount():
            return
        self.table.selectRow(index)
        self.table.scrollToItem(self.table.item(index, 0))
        self.table.setFocus()

    def _show_selected_record(self) -> None:
        index = self._selected_record_index()
        if index is None or not 0 <= index < len(self.records):
            self.preview.set_record(None)
            self.selected_file_label.setText("大图预览")
            self.candidate_value.setText("—")
            self.confidence_value.setText("置信度 —")
            self.record_status_value.setText("等待选择")
            self.record_error.hide()
            self._set_owner_choices("")
            self.owner_combo.setEnabled(False)
            self.confirm_button.setEnabled(False)
            return

        record = self.records[index]
        self.preview.set_record(record)
        self.selected_file_label.setText(record.file_name)
        candidate = record.owner or record.candidate_owner or "未识别"
        self.candidate_value.setText(candidate)
        self.confidence_value.setText(
            f"置信度 {record.confidence:.1%}" if record.confidence else "置信度 —"
        )
        self.record_status_value.setText(self._status_display(record.status)[0])
        self.record_error.setText(record.error)
        self.record_error.setVisible(bool(record.error))

        owners = self._set_owner_choices(candidate)
        actionable = (
            record.status in ACTIONABLE_STATUSES
            and self.session_output is not None
            and self.current_task_id is not None
        )
        self.owner_combo.setEnabled(actionable)
        self.confirm_button.setEnabled(actionable and bool(owners))

    def _set_owner_choices(self, target: str) -> list[str]:
        owners = self.database.owners()
        with QSignalBlocker(self.owner_combo):
            self.owner_combo.clear()
            self.owner_combo.addItems(owners)
            if target in owners:
                self.owner_combo.setCurrentText(target)
            else:
                self.owner_combo.setEditText("")
        return owners

    def _start(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.images:
            self._scan()
        if not self.images:
            return
        try:
            output_parent = Path(self.output_edit.text().strip()).resolve()
            self.session_output = ClassificationService.create_session_dir(output_parent)
            self.current_task_id = self.database.create_task(
                str(Path(self.input_edit.text()).resolve()), str(self.session_output)
            )
            self.records = [
                ClassificationRecord(source_path=str(path), task_id=self.current_task_id)
                for path in self.images
            ]
            self._populate_task_table()
            if self.records:
                self.table.selectRow(0)
            self.settings = self.database.load_settings()
            self.worker = BatchWorker(
                self.images,
                self.database.owner_alias_map(),
                self.settings,
                self.database,
                self.current_task_id,
                self.session_output,
                self,
            )
            self.worker.record_ready.connect(self._record_ready)
            self.worker.progress_changed.connect(self._progress_changed)
            self.worker.batch_finished.connect(self._batch_finished)
            self.worker.batch_error.connect(self._batch_error)
            self.worker.start()
            self.scan_button.setEnabled(False)
            self.start_button.setEnabled(True)
            self.start_button.setText("暂停")
            self.start_button.setIcon(self._icon(QStyle.SP_MediaPause))
            self.cancel_button.setEnabled(True)
            self.status_label.setText("正在加载 OCR 模型并识别…")
        except Exception as exc:
            QMessageBox.critical(self, "无法启动", str(exc))

    def _record_ready(self, record: ClassificationRecord, index: int) -> None:
        self.records[index] = record
        self._update_task_row(index, record)
        self._update_counts()
        self._apply_filters()

    def _progress_changed(self, current: int, total: int) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(f"正在识别 {current}/{total}")

    def _primary_action(self) -> None:
        if self.worker and self.worker.isRunning():
            self._toggle_pause()
        else:
            self._start()

    def _toggle_pause(self) -> None:
        if not self.worker:
            return
        self.paused = not self.paused
        if self.paused:
            self.worker.pause()
            self.start_button.setText("继续")
            self.start_button.setIcon(self._icon(QStyle.SP_MediaPlay))
            self.status_label.setText("任务已暂停")
        else:
            self.worker.resume()
            self.start_button.setText("暂停")
            self.start_button.setIcon(self._icon(QStyle.SP_MediaPause))

    def _cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.start_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在安全取消…")

    def _batch_finished(self, success: bool, message: str) -> None:
        self._set_idle_controls()
        self.status_label.setText(message)
        self.open_button.setEnabled(bool(self.session_output))
        self._update_counts()
        self._apply_filters()
        current = self._selected_record_index()
        if self._review_indexes() and (
            current is None or self.records[current].status not in ACTIONABLE_STATUSES
        ):
            self._select_record_index(self._review_indexes()[0])
        if not success:
            self._show_selected_record()

    def _batch_error(self, message: str) -> None:
        self._set_idle_controls()
        if self._close_after_worker:
            self.status_label.setText("任务已中断，正在退出…")
            return
        self.status_label.setText("任务失败")
        QMessageBox.critical(self, "识别失败", message)

    def _set_idle_controls(self) -> None:
        self.scan_button.setEnabled(True)
        self.start_button.setEnabled(bool(self.images))
        self.cancel_button.setEnabled(False)
        self.paused = False
        self.start_button.setText("开始识别")
        self.start_button.setIcon(self._icon(QStyle.SP_MediaPlay))

    def _update_counts(self) -> None:
        counts = Counter(record.status for record in self.records)
        automatic = sum(
            record.status == RecordStatus.CLASSIFIED and not record.reviewed
            for record in self.records
        )
        abnormal = counts[RecordStatus.UNRECOGNIZED] + counts[RecordStatus.FAILED]
        self.stat_total.setText(f"总数  {len(self.records)}")
        self.stat_classified.setText(f"高置信度  {automatic}")
        self.stat_review.setText(f"待复核  {counts[RecordStatus.REVIEW]}")
        self.stat_failed.setText(f"异常 / 未识别  {abnormal}")

    def _review_indexes(self) -> list[int]:
        return [
            index
            for index, record in enumerate(self.records)
            if record.status in ACTIONABLE_STATUSES
        ]

    def _refresh_review_table(
        self,
        select_index: int | None = None,
        select_row: int | None = None,
    ) -> None:
        self._apply_filters()
        if select_index is not None:
            self._select_record_index(select_index)
            return
        indexes = self._review_indexes()
        if indexes:
            position = min(max(select_row or 0, 0), len(indexes) - 1)
            self._select_record_index(indexes[position])
        elif self.records and self._selected_record_index() is None:
            self._select_record_index(0)
        else:
            self._show_selected_record()

    def _show_review_selection(self) -> None:
        self._show_selected_record()

    def _select_next_review(self, completed_index: int) -> None:
        indexes = self._review_indexes()
        if not indexes:
            self._select_record_index(completed_index)
            return
        next_index = next((index for index in indexes if index > completed_index), indexes[0])
        self._select_record_index(next_index)

    def _confirm_review(self) -> None:
        index = self._selected_record_index()
        if index is None or not self.session_output or self.current_task_id is None:
            return
        record = self.records[index]
        if record.status not in ACTIONABLE_STATUSES:
            return
        owner = self.owner_combo.currentText().strip()
        if owner not in self.database.owners():
            QMessageBox.warning(self, "无法确认", "请选择责任人名单中的有效姓名。")
            return
        try:
            old_output = Path(record.output_path) if record.output_path else None
            record.owner = owner
            record.reviewed = True
            record.status = RecordStatus.CONFIRMED
            ClassificationService(
                self.session_output, self.database.load_settings()
            ).classify(record, owner)
            if (
                old_output
                and old_output.parent.name == "未识别"
                and old_output.exists()
                and Path(record.output_path) != old_output
            ):
                old_output.unlink()
            self.database.save_record(record)
            self._update_task_row(index, record)
            self._update_counts()
            self._apply_filters()
            self._select_next_review(index)
            if not (self.worker and self.worker.isRunning()) and not self._review_indexes():
                self.database.update_task_status(self.current_task_id, "已完成")
        except Exception as exc:
            QMessageBox.critical(self, "分类失败", str(exc))

    def _open_output(self) -> None:
        if self.session_output and self.session_output.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session_output)))

    def _show_settings(self) -> None:
        self.settings_page.reload()
        self.settings_dialog.exec()

    def _settings_saved(self) -> None:
        self.settings = self.database.load_settings()
        self.status_label.setText("设置已自动保存")

    def _owners_changed(self) -> None:
        self._set_owner_choices(self.owner_combo.currentText())
        self._show_selected_record()
        self.status_label.setText("责任人名单已更新")

    def _open_update_settings(self) -> None:
        self.settings_page.reload()
        self.settings_page.show_update_tab(check=True)
        self.settings_dialog.exec()

    def _update_available(self, version: str) -> None:
        self.settings_button.setText(f"设置 · {version}")
        self.settings_button.setToolTip(f"发现新版本 {version}")

    def _confirm_cancel_and_close(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("识别任务正在运行")
        dialog.setText("识别尚未完成，是否中断任务并退出？")
        dialog.setInformativeText(
            "已完成的文件会保留；当前正在处理的文件将在安全结束后退出，不会直接强制终止文件操作。"
        )
        exit_button = dialog.addButton("中断并退出", QMessageBox.DestructiveRole)
        continue_button = dialog.addButton("继续识别", QMessageBox.RejectRole)
        dialog.setDefaultButton(continue_button)
        dialog.exec()
        return dialog.clickedButton() is exit_button

    def _finish_pending_close(self) -> None:
        if self._close_after_worker and not (self.worker and self.worker.isRunning()):
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            if self._close_after_worker:
                event.ignore()
                return
            if not self._confirm_cancel_and_close():
                event.ignore()
                return
            self._close_after_worker = True
            self.worker.finished.connect(self._finish_pending_close)
            self.worker.cancel()
            self.centralWidget().setEnabled(False)
            self.status_label.setText("正在安全中断，当前文件处理完成后将自动退出…")
            if not self.worker.isRunning():
                QTimer.singleShot(0, self.close)
            event.ignore()
            return
        if not self._database_closed:
            self.settings_page.shutdown()
            self.database.close()
            self._database_closed = True
        event.accept()
