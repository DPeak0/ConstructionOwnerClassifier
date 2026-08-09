from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QStringListModel, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QFileDialog, QFrame, QHBoxLayout,
    QGridLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__
from .database import Database
from .dialogs import SettingsDialog
from .models import ClassificationRecord, RecordStatus
from .parser import normalize_text
from .preview import ImagePreview
from .scanner import scan_images
from .services import ClassificationService
from .widgets import AnchoredComboBox, FlowWidget
from .worker import BatchWorker


APP_NAME = "施工责任人图片分类器"
PENDING_REVIEW_STATUSES = {
    RecordStatus.REVIEW,
    RecordStatus.UNRECOGNIZED,
    RecordStatus.FAILED,
}
CLASSIFIABLE_STATUSES = PENDING_REVIEW_STATUSES | {
    RecordStatus.CONFIRMED,
    RecordStatus.CLASSIFIED,
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


class SourceLineEdit(QLineEdit):
    paths_dropped = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.database = Database()
        self.settings = self.database.load_settings()
        self.input_sources: list[Path] = []
        self.images: list[Path] = []
        self.records: list[ClassificationRecord] = []
        self.current_task_id: int | None = None
        self.session_output: Path | None = None
        self.worker: BatchWorker | None = None
        self.paused = False
        self._close_after_worker = False
        self._update_shutdown_started = False
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
        self.workspace_splitter.splitterMoved.connect(
            lambda *_args: QTimer.singleShot(0, self._fit_task_columns)
        )
        QTimer.singleShot(0, self._fit_task_columns)

        if self.settings.update_auto_check and getattr(sys, "frozen", False):
            QTimer.singleShot(2500, lambda: self.settings_page.check_for_updates(silent=True))

    def _icon(self, standard: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(standard)

    def _command_button(
        self,
        text: str,
        icon: QStyle.StandardPixmap | None,
        slot,
        primary: bool = False,
    ) -> QPushButton:
        button = QPushButton(text)
        if icon is not None:
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
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([650, 650])
        self.workspace_splitter = splitter
        layout.addWidget(splitter, 1)
        layout.addWidget(self._build_footer())
        return root

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("headerBar")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        title = QLabel(APP_NAME)
        title.setObjectName("brandTitle")
        version = QLabel(f"版本 {__version__}")
        version.setObjectName("versionText")
        top_row.addWidget(title)
        top_row.addWidget(version)
        top_row.addStretch(1)
        self.settings_button = self._command_button(
            "设置", QStyle.SP_FileDialogDetailedView, self._show_settings
        )
        self.settings_button.setObjectName("quietButton")
        top_row.addWidget(self.settings_button)
        layout.addLayout(top_row)

        workflow = QGridLayout()
        workflow.setContentsMargins(0, 0, 0, 0)
        workflow.setHorizontalSpacing(12)
        workflow.setVerticalSpacing(0)
        workflow.setColumnStretch(0, 3)
        workflow.setColumnStretch(1, 3)

        input_box = QVBoxLayout()
        input_box.setSpacing(3)
        input_box.addWidget(self._field_label("输入来源"))
        input_row = QHBoxLayout()
        input_row.setSpacing(4)
        self.input_edit = SourceLineEdit()
        self.input_edit.setPlaceholderText("选择或拖入图片、文件夹")
        self.input_edit.paths_dropped.connect(self._add_input_sources)
        input_row.addWidget(self.input_edit, 1)
        self.source_button = QToolButton()
        self.source_button.setIcon(self._icon(QStyle.SP_DirOpenIcon))
        self.source_button.setToolTip("添加图片或文件夹")
        self.source_button.setFixedSize(42, 34)
        self.source_button.setPopupMode(QToolButton.InstantPopup)
        source_menu = QMenu(self.source_button)
        source_menu.addAction("添加图片…", self._choose_images)
        source_menu.addAction("添加文件夹…", self._choose_folder)
        source_menu.addSeparator()
        source_menu.addAction("清空选择", self._clear_input_sources)
        self.source_button.setMenu(source_menu)
        input_row.addWidget(self.source_button)
        input_box.addLayout(input_row)
        workflow.addLayout(input_box, 0, 0)

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
        workflow.addLayout(output_box, 0, 1)

        self.scan_button = self._command_button(
            "扫描", QStyle.SP_FileDialogContentsView, self._scan
        )
        self.start_button = self._command_button(
            "开始识别", QStyle.SP_MediaPlay, self._primary_action, True
        )
        self.cancel_button = self._command_button(
            "取消", QStyle.SP_BrowserStop, self._cancel
        )
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.scan_button.setMinimumWidth(76)
        self.start_button.setMinimumWidth(108)
        self.cancel_button.setMinimumWidth(76)
        actions.addWidget(self.scan_button)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        workflow.addLayout(actions, 0, 2, Qt.AlignBottom)
        layout.addLayout(workflow)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.scan_button.setEnabled(False)
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
        self.stat_no_watermark = self._stat_label("无水印", "statNoWatermark")
        self.stat_failed = self._stat_label("异常 / 未识别", "statFailed")
        for label in (
            self.stat_total,
            self.stat_classified,
            self.stat_review,
            self.stat_no_watermark,
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

        heading_row = QHBoxLayout()
        heading_row.setSpacing(8)
        heading = QLabel("任务列表")
        heading.setObjectName("sectionTitle")
        heading_row.addWidget(heading)
        self.task_count_label = QLabel("0 项")
        self.task_count_label.setObjectName("secondaryText")
        heading_row.addWidget(self.task_count_label)
        heading_row.addStretch(1)
        layout.addLayout(heading_row)
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文件名")
        self.search_edit.setClearButtonEnabled(True)
        self.search_completion_model = QStringListModel(self)
        self.search_completer = QCompleter(self.search_completion_model, self.search_edit)
        self.search_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.search_completer.setFilterMode(Qt.MatchContains)
        self.search_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.search_completer.setMaxVisibleItems(12)
        self.search_edit.setCompleter(self.search_completer)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.status_filter = AnchoredComboBox()
        self.status_filter.addItem("全部状态", "all")
        self.status_filter.addItem("仅待复核", "review")
        self.status_filter.addItem("已分类", "classified")
        self.status_filter.addItem("无水印", "no_watermark")
        self.status_filter.addItem("异常 / 未识别", "abnormal")
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.status_filter)
        layout.addLayout(filters)

        self.issue_banner = QLabel()
        self.issue_banner.setObjectName("issueBanner")
        self.issue_banner.setWordWrap(True)
        self.issue_banner.hide()
        layout.addWidget(self.issue_banner)

        self.table = TaskTable(0, 3)
        self.table.setHorizontalHeaderLabels(["文件名", "原始路径", "状态"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(88)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 270)
        self.table.setColumnWidth(2, 96)
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
        self.selected_file_label.setMinimumWidth(0)
        self.selected_file_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.selected_file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.selected_file_label)
        self.preview = ImagePreview()
        layout.addWidget(self.preview, 1)

        review = QFrame()
        review.setObjectName("reviewControls")
        review_layout = QVBoxLayout(review)
        review_layout.setContentsMargins(14, 12, 14, 12)
        review_layout.setSpacing(8)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(8)
        summary_row.addWidget(QLabel("识别责任人"))
        self.candidate_value = QLabel("—")
        self.candidate_value.setObjectName("candidateValue")
        summary_row.addWidget(self.candidate_value)
        summary_row.addStretch(1)
        self.confidence_value = QLabel("置信度 —")
        self.confidence_value.setObjectName("confidenceBadge")
        summary_row.addWidget(self.confidence_value)
        self.record_status_value = QLabel("等待选择")
        self.record_status_value.setObjectName("recordStatus")
        summary_row.addWidget(self.record_status_value)
        review_layout.addLayout(summary_row)

        candidate_section = QWidget()
        candidate_section.setObjectName("candidateSection")
        self.candidate_section = candidate_section
        candidate_section_layout = QVBoxLayout(candidate_section)
        candidate_section_layout.setContentsMargins(0, 0, 0, 0)
        candidate_section_layout.setSpacing(5)
        candidate_section_layout.addWidget(self._field_label("候选预选"))
        self.candidate_buttons: list[QPushButton] = []
        self.candidate_flow_widget = FlowWidget(spacing=6)
        self.candidate_flow = self.candidate_flow_widget.flow_layout
        self.candidate_empty_label = QLabel("暂无候选")
        self.candidate_empty_label.setObjectName("secondaryText")
        self.candidate_flow.addWidget(self.candidate_empty_label)
        candidate_section_layout.addWidget(self.candidate_flow_widget)
        review_layout.addWidget(candidate_section)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(QLabel("人工修正"))
        self.owner_combo = AnchoredComboBox()
        self.owner_combo.setEditable(True)
        self.owner_combo.setInsertPolicy(QComboBox.NoInsert)
        self.owner_combo.setMaxVisibleItems(12)
        self.owner_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.owner_combo.setMinimumHeight(36)
        self.owner_combo.setMinimumWidth(150)
        self.owner_combo.setMaximumWidth(320)
        self.owner_completer = QCompleter(self.owner_combo.model(), self.owner_combo)
        self.owner_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.owner_completer.setFilterMode(Qt.MatchContains)
        self.owner_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.owner_completer.setMaxVisibleItems(12)
        self.owner_combo.setCompleter(self.owner_completer)
        self.owner_combo.lineEdit().returnPressed.connect(self._confirm_review)
        self.owner_combo.addItems(self.database.owners())
        action_row.addWidget(self.owner_combo, 1)
        self.confirm_button = self._command_button(
            "确认分类", None, self._confirm_review, True
        )
        self.confirm_button.setEnabled(False)
        self.confirm_button.setMinimumWidth(96)
        action_row.addWidget(self.confirm_button)
        self.review_result_row = action_row
        review_layout.addLayout(action_row)

        self.record_error = QLabel()
        self.record_error.setObjectName("errorText")
        self.record_error.setWordWrap(True)
        self.record_error.hide()
        review_layout.addWidget(self.record_error)
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

    def _fit_task_columns(self) -> None:
        available = self.table.viewport().width() - 2
        if available <= 0:
            return
        status_width = max(96, self.table.columnWidth(2))
        file_width = max(140, self.table.columnWidth(0))
        if file_width + status_width + 160 > available:
            file_width = max(140, available - status_width - 160)
        path_width = max(160, available - file_width - status_width)
        self.table.setColumnWidth(0, file_width)
        self.table.setColumnWidth(1, path_width)
        self.table.setColumnWidth(2, status_width)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_task_columns)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QDialog, QWidget#dashboard {
                background:#f4f7fa; color:#1f2937;
                font-family:"Microsoft YaHei UI"; font-size:13px;
            }
            QFrame#headerBar, QFrame#reviewControls {
                background:#ffffff; border:1px solid #d7e0e8; border-radius:6px;
            }
            QFrame#statsBar {
                background:#ffffff; border:1px solid #dfe6ed; border-radius:4px;
            }
            QLabel#brandTitle { color:#10233d; font-size:18px; font-weight:700; }
            QLabel#versionText, QLabel#fieldLabel, QLabel#secondaryText { color:#64748b; }
            QLabel#versionText {
                background:#eef2f6; border-radius:3px; padding:3px 7px; font-size:11px;
            }
            QLabel#fieldLabel { font-size:12px; font-weight:600; }
            QLabel#sectionTitle { color:#172033; font-size:14px; font-weight:700; padding:2px 0; }
            QLabel#statTotal, QLabel#statClassified, QLabel#statReview,
            QLabel#statNoWatermark, QLabel#statFailed {
                background:#ffffff; border:0; border-right:1px solid #e5eaf0;
                color:#334155; font-weight:600; padding:1px 8px;
            }
            QLabel#statTotal { border-bottom:2px solid #64748b; }
            QLabel#statClassified { color:#166534; border-bottom:2px solid #22a06b; }
            QLabel#statReview { color:#8a5d00; border-bottom:2px solid #e5a000; }
            QLabel#statNoWatermark { color:#075985; border-bottom:2px solid #38bdf8; }
            QLabel#statFailed { color:#b42318; border-right:0; border-bottom:2px solid #dc4c3f; }
            QLabel#candidateValue { color:#10233d; font-size:15px; font-weight:700; }
            QLabel#confidenceBadge, QLabel#recordStatus {
                background:#eef2f6; color:#475569; border-radius:4px; padding:4px 8px;
            }
            QLabel#errorText { color:#b42318; }
            QLabel#issueBanner {
                background:#fff8e6; color:#7c5200; border:1px solid #efcf86;
                border-radius:4px; padding:7px 9px;
            }
            QFrame#listPanel, QFrame#inspectorPanel, QFrame#footerBar { border:0; background:transparent; }
            QScrollArea#imagePreview, QLabel#imageCanvas {
                background:#111827; color:#aab5bd; border:0;
            }
            QScrollArea#imagePreview { border:1px solid #273244; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
                background:#ffffff; border:1px solid #bdc8d3; border-radius:4px;
                min-height:20px; padding:6px 8px; selection-background-color:#2563eb;
            }
            QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
            QTextEdit:hover { border-color:#94a3b8; }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QTextEdit:focus { border:1px solid #2563eb; }
            QComboBox QAbstractItemView {
                background:#ffffff; border:1px solid #cbd5e1; outline:0;
                padding:4px; selection-background-color:#dbeafe;
                selection-color:#172033;
            }
            QCompleter QAbstractItemView {
                background:#ffffff; border:1px solid #cbd5e1; outline:0;
                padding:4px; selection-background-color:#dbeafe;
                selection-color:#172033;
            }
            QMenu {
                background:#ffffff; border:1px solid #cbd5e1; padding:5px;
            }
            QMenu::item { padding:7px 28px 7px 10px; border-radius:3px; }
            QMenu::item:selected { background:#dbeafe; color:#172033; }
            QMenu::separator { height:1px; background:#e2e8f0; margin:5px 3px; }
            QPushButton {
                background:#ffffff; border:1px solid #b8c4cf; border-radius:4px;
                min-height:20px; padding:6px 12px;
            }
            QPushButton:pressed, QToolButton:pressed { background:#dbeafe; }
            QPushButton:hover, QToolButton:hover { background:#eff6ff; border-color:#2563eb; }
            QPushButton:disabled { color:#98a2ad; background:#edf0f3; border-color:#d8dee4; }
            QPushButton#primaryButton {
                background:#2563eb; color:#ffffff; border-color:#2563eb; font-weight:600;
            }
            QPushButton#primaryButton:hover { background:#1d4ed8; }
            QPushButton#quietButton {
                background:transparent; border-color:transparent; color:#475569;
            }
            QPushButton#quietButton:hover { background:#eef2f6; border-color:#d7e0e8; }
            QPushButton#candidateButton {
                background:#f8fafc; color:#1d4ed8; border-color:#bfdbfe;
                border-radius:4px; padding:3px 7px; min-height:20px;
            }
            QPushButton#candidateButton:hover { background:#eff6ff; border-color:#2563eb; }
            QPushButton#dangerButton { color:#b42318; border-color:#f0b4ae; }
            QPushButton#dangerButton:hover { background:#fff1f0; border-color:#d92d20; }
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
                outline:0;
            }
            QTableWidget::item { padding:5px 7px; border-bottom:1px solid #edf1f4; }
            QTableWidget::item:selected { background:#dbeafe; color:#172033; }
            QProgressBar {
                background:#e2e8f0; border:0; border-radius:3px; height:13px; text-align:center;
            }
            QProgressBar::chunk { background:#2f855a; border-radius:3px; }
            QSplitter::handle { background:transparent; width:8px; }
            QSplitter::handle:hover { background:#dbeafe; }
            QTabWidget::pane { background:#ffffff; border:1px solid #d6dee6; }
            QTabBar::tab {
                background:#eef2f6; color:#64748b; min-width:96px;
                padding:10px 18px; margin-right:1px; border:0;
            }
            QTabBar::tab:hover { background:#e2e8f0; color:#334155; }
            QTabBar::tab:selected {
                background:#ffffff; color:#1d4ed8; font-weight:600;
                border-top:2px solid #2563eb;
            }
            QCheckBox { spacing:8px; }
            QScrollBar:vertical { background:#f1f5f9; width:10px; margin:0; }
            QScrollBar::handle:vertical {
                background:#aebccc; min-height:28px; border-radius:5px;
            }
            QScrollBar::handle:vertical:hover { background:#8294a8; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar:horizontal { background:#f1f5f9; height:10px; margin:0; }
            QScrollBar::handle:horizontal {
                background:#aebccc; min-width:28px; border-radius:5px;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
            """
        )

    def _input_dialog_start(self) -> str:
        if not self.input_sources:
            return ""
        first = self.input_sources[0]
        return str(first if first.is_dir() else first.parent)

    def _choose_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "添加施工图片",
            self._input_dialog_start(),
            "图片文件 (*.jpg *.jpeg *.jfif *.png *.bmp *.webp *.tif *.tiff)",
        )
        self._add_input_sources(files)

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "添加图片文件夹", self._input_dialog_start()
        )
        if directory:
            self._add_input_sources([directory])

    def _choose_input(self) -> None:
        self._choose_folder()

    def _add_input_sources(self, values: list[str | Path] | tuple[str | Path, ...]) -> None:
        sources = list(self.input_sources)
        seen = {os.path.normcase(str(path)) for path in sources}
        for value in values:
            path = Path(value).expanduser().resolve()
            key = os.path.normcase(str(path))
            if path.exists() and key not in seen:
                sources.append(path)
                seen.add(key)
        self._set_input_sources(sources)

    def _set_input_sources(self, sources: list[Path]) -> None:
        self.input_sources = sources
        if not sources:
            self.input_edit.clear()
            self.input_edit.setToolTip("")
        elif len(sources) == 1:
            self.input_edit.setText(native_path_text(sources[0]))
            self.input_edit.setToolTip(native_path_text(sources[0]))
        else:
            names = "；".join(path.name or native_path_text(path) for path in sources[:3])
            suffix = "…" if len(sources) > 3 else ""
            self.input_edit.setText(f"已选择 {len(sources)} 项：{names}{suffix}")
            self.input_edit.setToolTip("\n".join(native_path_text(path) for path in sources))

        self.images = []
        self.records = []
        self.current_task_id = None
        self.session_output = None
        self._populate_task_table()
        self._show_selected_record()
        self.start_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.scan_button.setEnabled(bool(sources))
        self._update_counts()
        if sources and not self.output_edit.text():
            first = sources[0]
            self.output_edit.setText(native_path_text(first.parent))

    def _clear_input_sources(self) -> None:
        self._set_input_sources([])
        self.status_label.setText("等待选择图片或文件夹")

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择结果保存位置", self.output_edit.text()
        )
        if directory:
            self.output_edit.setText(native_path_text(directory))

    def _scan(self) -> None:
        if not self.input_sources:
            self.scan_button.setEnabled(False)
            self.status_label.setText("请先选择图片或文件夹")
            return
        try:
            output_dir = (
                Path(self.output_edit.text().strip()).expanduser().resolve()
                if self.output_edit.text().strip()
                else self.input_sources[0].parent
            )
            self.output_edit.setText(native_path_text(output_dir))
            self.images = scan_images(self.input_sources, output_dir)
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
        self.search_completion_model.setStringList(
            list(dict.fromkeys(record.file_name for record in self.records))
        )
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
            RecordStatus.NO_WATERMARK: ("无水印", "#e0f2fe", "#075985"),
            RecordStatus.FAILED: ("异常", "#fee2e2", "#b42318"),
        }
        return values[status]

    def _update_task_row(
        self, row: int, record: ClassificationRecord, load_thumbnail: bool = True
    ) -> None:
        del load_thumbnail
        file_item = QTableWidgetItem(record.file_name)
        file_item.setData(Qt.UserRole, row)
        path_item = QTableWidgetItem(native_path_text(record.source_path))
        path_item.setToolTip(native_path_text(record.source_path))
        status_text, background, foreground = self._status_display(record.status)
        status_item = QTableWidgetItem(status_text)
        status_item.setTextAlignment(Qt.AlignCenter)
        status_item.setBackground(QColor(background))
        status_item.setForeground(QColor(foreground))
        self.table.setItem(row, 0, file_item)
        self.table.setItem(row, 1, path_item)
        self.table.setItem(row, 2, status_item)
        if self._selected_record_index() == row:
            self._show_selected_record()

    def _matches_status_filter(self, record: ClassificationRecord) -> bool:
        mode = self.status_filter.currentData()
        if mode == "review":
            return record.status == RecordStatus.REVIEW
        if mode == "classified":
            return record.status in {RecordStatus.CLASSIFIED, RecordStatus.CONFIRMED}
        if mode == "no_watermark":
            return record.status == RecordStatus.NO_WATERMARK
        if mode == "abnormal":
            return record.status in {RecordStatus.UNRECOGNIZED, RecordStatus.FAILED}
        return True

    def _apply_filters(self, *_args) -> None:
        query = self.search_edit.text().strip().casefold()
        visible_count = 0
        for row, record in enumerate(self.records):
            visible = (not query or query in record.file_name.casefold()) and self._matches_status_filter(record)
            self.table.setRowHidden(row, not visible)
            visible_count += int(visible)
        self.task_count_label.setText(
            f"{visible_count} / {len(self.records)} 项"
            if visible_count != len(self.records)
            else f"{len(self.records)} 项"
        )
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
            self.selected_file_label.setToolTip("")
            self.candidate_value.setText("—")
            self.confidence_value.setText("置信度 —")
            self.record_status_value.setText("等待选择")
            self.record_error.hide()
            self._set_owner_choices("")
            self.owner_combo.setEnabled(False)
            self.confirm_button.setEnabled(False)
            self.confirm_button.setText("确认分类")
            self._set_candidate_choices(None, False)
            self.issue_banner.hide()
            return

        record = self.records[index]
        issue = self._review_issue(record)
        self.issue_banner.setText(issue)
        self.issue_banner.setVisible(bool(issue))
        self.preview.set_record(record)
        self.selected_file_label.setText(record.file_name)
        self.selected_file_label.setToolTip(record.file_name)
        candidate = record.owner or record.candidate_owner
        if not candidate and record.candidate_owners:
            candidate = record.candidate_owners[0][0]
        self.candidate_value.setText(candidate or "未识别")
        confidence_text = (
            f"置信度 {record.confidence:.1%}" if record.confidence else "置信度 —"
        )
        if record.status == RecordStatus.REVIEW and record.owner_margin < 0.12:
            confidence_text += f" · 名单内分差 {record.owner_margin:.1%}"
        self.confidence_value.setText(confidence_text)
        self.record_status_value.setText(self._status_display(record.status)[0])
        self.record_error.setText(record.error)
        self.record_error.setVisible(bool(record.error))

        owners = self._set_owner_choices(candidate)
        actionable = (
            record.status in CLASSIFIABLE_STATUSES
            and self.session_output is not None
            and self.current_task_id is not None
        )
        self.owner_combo.setEnabled(actionable)
        self.confirm_button.setEnabled(actionable and bool(self.owner_combo.currentText().strip()))
        self.confirm_button.setText(
            "重新分类"
            if record.status in {RecordStatus.CLASSIFIED, RecordStatus.CONFIRMED}
            else "确认分类"
        )
        self._set_candidate_choices(record, actionable)

    @staticmethod
    def _review_issue(record: ClassificationRecord) -> str:
        if record.status == RecordStatus.FAILED:
            return "问题：图片读取或 OCR 处理失败"
        if record.status == RecordStatus.UNRECOGNIZED:
            if record.decision_source == "ai_filtered_candidates":
                return "问题：候选词均被判断为非姓名"
            if record.ai_error:
                return "问题：AI 辅助不可用，需人工判断"
            if record.watermark_score >= 0.55:
                return "问题：检测到责任人水印，但姓名不可读"
            return "问题：未找到可信的责任人姓名"
        if record.status == RecordStatus.REVIEW:
            if record.decision_source == "name_spelling_ambiguity":
                return "问题：识别姓名与名单姓名仅一字不同，需人工确认"
            if record.decision_source == "ai_visual_review":
                return "问题：AI 兜底识别结果需人工确认"
            if record.decision_source == "ai_semantic_conflict":
                return "问题：AI 与本地候选判断不一致"
            if record.owner_margin < 0.12 and record.candidate_owner:
                return "问题：名单内候选接近，需人工确认"
            if not record.candidate_owner:
                return "问题：识别到名单外姓名，需人工确认"
            return "问题：姓名置信度不足，需人工确认"
        return ""

    def _set_owner_choices(self, target: str) -> list[str]:
        owners = self.database.owners()
        with QSignalBlocker(self.owner_combo):
            self.owner_combo.clear()
            self.owner_combo.addItems(owners)
            if target in owners:
                self.owner_combo.setCurrentText(target)
            else:
                self.owner_combo.setEditText(target)
        return owners

    def _set_candidate_choices(
        self, record: ClassificationRecord | None, actionable: bool
    ) -> None:
        for button in self.candidate_buttons:
            self.candidate_flow.removeWidget(button)
            button.deleteLater()
        self.candidate_buttons = []
        candidates: list[tuple[str, float]] = []
        if record is not None:
            candidates = list(record.candidate_owners)
            if record.candidate_owner and not any(
                name.strip() == record.candidate_owner.strip() for name, _score in candidates
            ):
                candidates.append((record.candidate_owner, record.confidence))
        candidates = sorted(
            ((name.strip(), float(score)) for name, score in candidates if name.strip()),
            key=lambda item: item[1], reverse=True,
        )
        self.candidate_empty_label.setVisible(not candidates)
        for name, score in candidates:
            button_text = f"{name}  {score:.0%}"
            button = QPushButton()
            button.setText(button.fontMetrics().elidedText(button_text, Qt.ElideRight, 132))
            button.setObjectName("candidateButton")
            button.setToolTip(f"选择候选责任人 {name}（置信度 {score:.1%}）")
            button.setMaximumWidth(150)
            button.setFixedHeight(30)
            button.setEnabled(actionable)
            button.clicked.connect(
                lambda _checked=False, value=name: self._select_candidate_owner(value)
            )
            self.candidate_flow.addWidget(button)
            self.candidate_buttons.append(button)
        self.candidate_flow.invalidate()
        self.candidate_flow_widget.refreshHeight()
        self.candidate_section.updateGeometry()

    def _select_candidate_owner(self, owner: str) -> None:
        self.owner_combo.setCurrentText(owner)
        self.owner_combo.setFocus()
        self.confirm_button.setEnabled(bool(owner.strip()) and self.owner_combo.isEnabled())

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
                "; ".join(native_path_text(path) for path in self.input_sources),
                str(self.session_output),
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
            self.source_button.setEnabled(False)
            self.input_edit.setEnabled(False)
            self.start_button.setEnabled(True)
            self.start_button.setText("暂停")
            self.start_button.setIcon(self._icon(QStyle.SP_MediaPause))
            self.cancel_button.setEnabled(True)
            self.status_label.setText("正在加载 OCR 模型并识别…")
        except Exception as exc:
            QMessageBox.critical(self, "无法启动", str(exc))

    def _record_ready(self, record: ClassificationRecord, index: int) -> None:
        self.records[index] = record
        self._reconcile_record_with_owner_list(record)
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
        if success and self.current_task_id is not None and not self._review_indexes():
            self.database.update_task_status(self.current_task_id, "已完成")
        current = self._selected_record_index()
        if self._review_indexes() and (
            current is None or self.records[current].status not in PENDING_REVIEW_STATUSES
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
        self.scan_button.setEnabled(bool(self.input_sources))
        self.source_button.setEnabled(True)
        self.input_edit.setEnabled(True)
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
        self.stat_no_watermark.setText(f"无水印  {counts[RecordStatus.NO_WATERMARK]}")
        self.stat_failed.setText(f"异常 / 未识别  {abnormal}")

    def _review_indexes(self) -> list[int]:
        return [
            index
            for index, record in enumerate(self.records)
            if record.status in PENDING_REVIEW_STATUSES
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
        if record.status not in CLASSIFIABLE_STATUSES:
            return
        owner = self.owner_combo.currentText().strip()
        if not owner:
            QMessageBox.warning(self, "无法确认", "请输入或选择责任人姓名。")
            return
        owner_added = False
        if owner not in self.database.owners():
            decision = self._ask_unknown_owner_decision(owner)
            if decision == "cancel":
                return
            if decision == "add":
                try:
                    self.database.add_owner(owner)
                    owner_added = True
                except Exception as exc:
                    QMessageBox.warning(self, "添加责任人失败", str(exc))
                    return
                self._set_owner_choices(owner)
                self._refresh_worker_owners()
        previous = (
            record.owner, record.reviewed, record.status, record.output_path,
            record.sha256, record.processed_at,
        )
        try:
            record.owner = owner
            ClassificationService(
                self.session_output, self.database.load_settings()
            ).reclassify(record, owner)
            record.reviewed = True
            self.database.save_record(record)
            promoted = self._reconcile_pending_records(exclude_index=index) if owner_added else 0
            self._update_task_row(index, record)
            self._update_counts()
            self._apply_filters()
            self._select_next_review(index)
            if promoted:
                self.status_label.setText(f"已添加责任人，并自动分类 {promoted} 个同名任务")
            if not (self.worker and self.worker.isRunning()) and not self._review_indexes():
                self.database.update_task_status(self.current_task_id, "已完成")
        except Exception as exc:
            (
                record.owner, record.reviewed, record.status, record.output_path,
                record.sha256, record.processed_at,
            ) = previous
            QMessageBox.critical(self, "分类失败", str(exc))

    def _ask_unknown_owner_decision(self, owner: str) -> str:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("责任人不在名单")
        dialog.setText(f"当前责任人【{owner}】不在责任人名单内")
        dialog.setInformativeText("请选择后续操作")
        add_button = dialog.addButton("添加并分类", QMessageBox.AcceptRole)
        classify_button = dialog.addButton("仅分类", QMessageBox.ActionRole)
        cancel_button = dialog.addButton("取消", QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is add_button:
            return "add"
        if clicked is classify_button:
            return "classify"
        return "cancel"

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
        self._refresh_worker_owners()
        promoted = self._reconcile_pending_records()
        self._set_owner_choices(self.owner_combo.currentText())
        self._show_selected_record()
        self.status_label.setText(
            f"责任人名单已更新，自动分类 {promoted} 个任务"
            if promoted else "责任人名单已更新"
        )

    def _refresh_worker_owners(self) -> None:
        if self.worker and hasattr(self.worker, "update_owners"):
            self.worker.update_owners(self.database.owner_alias_map())

    def _matching_current_owner(
        self, record: ClassificationRecord,
    ) -> tuple[str, float, float] | None:
        if record.status not in {RecordStatus.REVIEW, RecordStatus.UNRECOGNIZED}:
            return None
        owner_map = self.database.owner_alias_map()
        spellings = {
            normalize_text(spelling): owner
            for owner, aliases in owner_map.items()
            for spelling in (owner, *aliases)
        }
        candidates = list(record.candidate_owners)
        if record.candidate_owner and not any(
            normalize_text(name) == normalize_text(record.candidate_owner)
            for name, _score in candidates
        ):
            candidates.append((record.candidate_owner, record.confidence))
        if (
            record.decision_source == "name_spelling_ambiguity"
            and any(normalize_text(name) not in spellings for name, _score in candidates)
        ):
            return None
        known_scores: dict[str, float] = {}
        for name, score in candidates:
            owner = spellings.get(normalize_text(name))
            if owner:
                known_scores[owner] = max(known_scores.get(owner, 0.0), float(score))
        if not known_scores:
            return None
        ordered = sorted(known_scores.items(), key=lambda item: item[1], reverse=True)
        owner, score = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else 0.0
        margin = score - second
        settings = self.database.load_settings()
        if (
            score < settings.auto_threshold
            or margin < 0.12
            or record.watermark_score < 0.72
        ):
            return None
        return owner, score, margin

    def _reconcile_record_with_owner_list(
        self, record: ClassificationRecord,
    ) -> bool:
        if self.session_output is None or record.task_id is None:
            return False
        match = self._matching_current_owner(record)
        if match is None:
            return False
        owner, score, margin = match
        previous = (
            record.owner, record.candidate_owner, record.confidence,
            record.local_confidence, record.owner_margin, record.status,
            record.output_path, record.sha256, record.decision_source,
        )
        try:
            record.candidate_owner = owner
            record.confidence = score
            record.local_confidence = max(record.local_confidence, score)
            record.owner_margin = margin
            record.owner = owner
            record.decision_source = "owner_list_refresh"
            service = ClassificationService(
                self.session_output, self.database.load_settings()
            )
            if record.output_path:
                service.reclassify(record, owner)
            else:
                service.classify(record, owner)
            record.reviewed = False
            self.database.save_record(record)
            return True
        except Exception as exc:
            (
                record.owner, record.candidate_owner, record.confidence,
                record.local_confidence, record.owner_margin, record.status,
                record.output_path, record.sha256, record.decision_source,
            ) = previous
            record.error = f"名单更新后自动分类失败：{exc}"
            return False

    def _reconcile_pending_records(self, exclude_index: int | None = None) -> int:
        promoted = 0
        for index, record in enumerate(self.records):
            if index == exclude_index:
                continue
            if self._reconcile_record_with_owner_list(record):
                promoted += 1
                self._update_task_row(index, record)
        if promoted:
            self._update_counts()
            self._apply_filters()
            if self.current_task_id is not None and not self._review_indexes():
                self.database.update_task_status(self.current_task_id, "已完成")
        return promoted

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
        batch_running = bool(self.worker and self.worker.isRunning())
        updates_running = bool(self.settings_page.running_update_workers())
        if self._close_after_worker and not batch_running and not updates_running:
            QTimer.singleShot(0, self.close)

    def _request_update_shutdown(self) -> None:
        if self._update_shutdown_started:
            return
        update_workers = self.settings_page.request_update_shutdown()
        if not update_workers:
            return
        self._update_shutdown_started = True
        for update_worker in update_workers:
            update_worker.finished.connect(self._finish_pending_close)

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
            self._request_update_shutdown()
            self.centralWidget().setEnabled(False)
            self.status_label.setText("正在安全中断，当前文件处理完成后将自动退出…")
            if not self.worker.isRunning():
                QTimer.singleShot(0, self.close)
            event.ignore()
            return
        update_workers = self.settings_page.running_update_workers()
        if update_workers:
            self._close_after_worker = True
            self._request_update_shutdown()
            self.centralWidget().setEnabled(False)
            self.status_label.setText("正在安全停止更新任务，完成后将自动退出…")
            event.ignore()
            return
        if not self._database_closed:
            self.settings_page.shutdown()
            self.database.close()
            self._database_closed = True
        event.accept()
