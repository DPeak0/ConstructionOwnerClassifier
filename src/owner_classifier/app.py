from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QSignalBlocker, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QStyle, QTableWidget, QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__
from .database import Database
from .dialogs import SettingsPage
from .models import ClassificationRecord, RecordStatus
from .scanner import scan_images
from .services import ClassificationService
from .single_instance import SingleInstanceGuard
from .worker import BatchWorker


APP_NAME = "施工责任人图片分类器"
INSTANCE_MUTEX_NAME = "Local\\ConstructionOwnerClassifier-5E4A77E2-62B8-4F57-9509-93B9EA343B22"


def native_path_text(value: str | Path) -> str:
    return os.path.normpath(str(value))


class PreviewCanvas(QLabel):
    zoom_requested = Signal(int, object, object)
    pan_requested = Signal(object)
    double_click_requested = Signal(object, object)

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._drag_position: QPoint | None = None
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, event) -> None:
        viewport = self.parentWidget()
        viewport_position = self.mapTo(viewport, event.position().toPoint()) if viewport else event.position().toPoint()
        self.zoom_requested.emit(
            1 if event.angleDelta().y() > 0 else -1,
            event.position(), QPointF(viewport_position),
        )
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            viewport = self.parentWidget()
            viewport_position = self.mapTo(viewport, event.position().toPoint()) if viewport else event.position().toPoint()
            self.double_click_requested.emit(event.position(), QPointF(viewport_position))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_position = event.globalPosition().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_position is not None and event.buttons() & Qt.LeftButton:
            current = event.globalPosition().toPoint()
            self.pan_requested.emit(current - self._drag_position)
            self._drag_position = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_position is not None:
            self._drag_position = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ImagePreview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._source = QPixmap()
        self._scale = 1.0
        self._fit_mode = True
        self.current_source = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch(1)
        self.zoom_out_button = self._tool_button("-", "缩小图片", self.zoom_out)
        self.actual_button = self._tool_button("1:1", "显示原始大小", self.actual_size)
        self.fit_button = self._tool_button("适应", "适应预览窗口", self.fit_to_window)
        self.zoom_in_button = self._tool_button("+", "放大图片", self.zoom_in)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        for widget in (
            self.zoom_out_button, self.actual_button, self.fit_button,
            self.zoom_in_button, self.zoom_label,
        ):
            toolbar.addWidget(widget)
        layout.addLayout(toolbar)

        self.canvas = PreviewCanvas("选择待复核记录")
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setObjectName("imageCanvas")
        self.canvas.zoom_requested.connect(self._wheel_zoom)
        self.canvas.pan_requested.connect(self._pan)
        self.canvas.double_click_requested.connect(self._double_click_zoom)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("imagePreview")
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidgetResizable(False)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)

    def _tool_button(self, text: str, tooltip: str, slot) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedHeight(28)
        button.clicked.connect(slot)
        return button

    def set_record(self, record: ClassificationRecord | None) -> None:
        image_path = None
        if record is not None:
            image_path = next(
                (path for path in (Path(record.source_path), Path(record.output_path) if record.output_path else None)
                 if path is not None and path.is_file()),
                None,
            )
        if record is None or image_path is None:
            self._source = QPixmap()
            self.current_source = ""
            self.canvas.clear()
            self.canvas.setText("图片不可用" if record else "选择待复核记录")
            self.canvas.resize(self.scroll.viewport().size())
            return
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self.current_source = ""
            self.canvas.setText("无法加载图片")
            return
        if record.rotation:
            pixmap = pixmap.transformed(QTransform().rotate(-record.rotation), Qt.SmoothTransformation)
        try:
            blocks = json.loads(record.ocr_blocks or "[]")
        except json.JSONDecodeError:
            blocks = []
        if blocks:
            painted = QPixmap(pixmap)
            painter = QPainter(painted)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#e05252"), max(2, painted.width() // 700)))
            for block in blocks:
                points = block.get("box", [])
                if len(points) >= 4:
                    painter.drawPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in points]))
            painter.end()
            pixmap = painted
        self._source = pixmap
        self.current_source = str(image_path)
        self.fit_to_window()

    @property
    def scale_factor(self) -> float:
        return self._scale

    def zoom_in(self) -> None:
        self._set_scale(self._scale * 1.25)

    def zoom_out(self) -> None:
        self._set_scale(self._scale / 1.25)

    def actual_size(self) -> None:
        self._set_scale(1.0)

    def fit_to_window(self) -> None:
        if self._source.isNull():
            return
        available = self.scroll.viewport().size()
        if available.width() <= 1 or available.height() <= 1:
            return
        available.setWidth(max(1, available.width() - 6))
        available.setHeight(max(1, available.height() - 6))
        self._fit_mode = True
        self._scale = min(
            available.width() / self._source.width(),
            available.height() / self._source.height(),
            1.0,
        )
        self._render()

    def _wheel_zoom(self, direction: int, image_position: QPointF, viewport_position: QPointF) -> None:
        factor = 1.15 if direction > 0 else 1 / 1.15
        self._set_scale(self._scale * factor, image_position, viewport_position)

    def _double_click_zoom(self, image_position: QPointF, viewport_position: QPointF) -> None:
        self._set_scale(self._scale * 2.0, image_position, viewport_position)

    def _pan(self, delta: QPoint) -> None:
        horizontal = self.scroll.horizontalScrollBar()
        vertical = self.scroll.verticalScrollBar()
        horizontal.setValue(horizontal.value() - delta.x())
        vertical.setValue(vertical.value() - delta.y())

    def _set_scale(
        self, scale: float, image_position: QPointF | None = None,
        viewport_position: QPointF | None = None,
    ) -> None:
        if self._source.isNull():
            return
        old_width = max(self.canvas.width(), 1)
        old_height = max(self.canvas.height(), 1)
        anchor_x = image_position.x() / old_width if image_position is not None else None
        anchor_y = image_position.y() / old_height if image_position is not None else None
        self._fit_mode = False
        self._scale = max(0.10, min(scale, 8.0))
        self._render(preserve_center=image_position is None)
        if anchor_x is not None and anchor_y is not None and viewport_position is not None:
            self.scroll.horizontalScrollBar().setValue(
                int(anchor_x * self.canvas.width() - viewport_position.x())
            )
            self.scroll.verticalScrollBar().setValue(
                int(anchor_y * self.canvas.height() - viewport_position.y())
            )

    def _render(self, preserve_center: bool = False) -> None:
        if self._source.isNull():
            return
        horizontal = self.scroll.horizontalScrollBar()
        vertical = self.scroll.verticalScrollBar()
        center_x = (horizontal.value() + horizontal.pageStep() / 2) / max(horizontal.maximum() + horizontal.pageStep(), 1)
        center_y = (vertical.value() + vertical.pageStep() / 2) / max(vertical.maximum() + vertical.pageStep(), 1)
        size = self._source.size() * self._scale
        pixmap = self._source.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.canvas.setPixmap(pixmap)
        self.canvas.resize(pixmap.size())
        self.zoom_label.setText(f"{self._scale:.0%}")
        if preserve_center:
            horizontal.setValue(int(center_x * (horizontal.maximum() + horizontal.pageStep()) - horizontal.pageStep() / 2))
            vertical.setValue(int(center_y * (vertical.maximum() + vertical.pageStep()) - vertical.pageStep() / 2))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            QTimer.singleShot(0, self.fit_to_window)


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

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 880)
        self.setMinimumSize(1120, 700)
        self.setCentralWidget(self._build_shell())
        self._apply_style()
        if self.settings.update_auto_check and getattr(sys, "frozen", False):
            QTimer.singleShot(2500, lambda: self.settings_page.check_for_updates(silent=True))

    def _icon(self, standard: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(standard)

    def _command_button(self, text: str, icon: QStyle.StandardPixmap, slot, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setIcon(self._icon(icon))
        button.setMinimumHeight(34)
        if primary:
            button.setObjectName("primaryButton")
        button.clicked.connect(slot)
        return button

    def _build_shell(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_task_page())
        self.pages.addWidget(self._build_review_page())
        self.settings_page = SettingsPage(self.database)
        self.settings_page.settings_saved.connect(self._settings_saved)
        self.settings_page.owners_changed.connect(self._owners_changed)
        self.settings_page.update_available.connect(self._update_available)
        self.pages.addWidget(self.settings_page)
        layout.addWidget(self.pages, 1)
        return root

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 20, 14, 16)
        layout.setSpacing(8)
        brand = QLabel("施工责任人\n图片分类器")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        layout.addSpacing(16)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_specs = [
            ("分类任务", QStyle.SP_FileDialogContentsView),
            ("待复核", QStyle.SP_MessageBoxWarning),
            ("设置", QStyle.SP_FileDialogDetailedView),
        ]
        self.nav_buttons: list[QPushButton] = []
        for index, (text, icon) in enumerate(nav_specs):
            button = QPushButton(text)
            button.setIcon(self._icon(icon))
            button.setCheckable(True)
            button.setObjectName("navButton")
            button.setMinimumHeight(42)
            button.clicked.connect(lambda checked=False, page=index: self._switch_page(page))
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            layout.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch(1)
        self.version_button = QPushButton(f"版本 {__version__}")
        self.version_button.setObjectName("sidebarVersion")
        self.version_button.setToolTip("检查软件更新")
        self.version_button.clicked.connect(self._open_update_settings)
        layout.addWidget(self.version_button)
        return sidebar

    def _page_header(self, title: str, subtitle: str) -> tuple[QLabel, QLabel]:
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        detail = QLabel(subtitle)
        detail.setObjectName("pageSubtitle")
        return heading, detail

    def _build_task_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        heading, subtitle = self._page_header("分类任务", "选择施工照片目录并按责任人批量整理")
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        paths = QGridLayout()
        paths.setColumnStretch(1, 1)
        paths.setHorizontalSpacing(10)
        paths.setVerticalSpacing(8)
        paths.addWidget(QLabel("输入目录"), 0, 0)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择包含施工照片的目录")
        paths.addWidget(self.input_edit, 0, 1)
        choose_input = self._command_button("选择", QStyle.SP_DirOpenIcon, self._choose_input)
        paths.addWidget(choose_input, 0, 2)
        paths.addWidget(QLabel("结果位置"), 1, 0)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择分类结果保存位置")
        paths.addWidget(self.output_edit, 1, 1)
        choose_output = self._command_button("选择", QStyle.SP_DirOpenIcon, self._choose_output)
        paths.addWidget(choose_output, 1, 2)
        layout.addLayout(paths)

        commands = QHBoxLayout()
        self.scan_button = self._command_button("扫描", QStyle.SP_FileDialogContentsView, self._scan)
        self.start_button = self._command_button("开始识别", QStyle.SP_MediaPlay, self._start, True)
        self.pause_button = self._command_button("暂停", QStyle.SP_MediaPause, self._toggle_pause)
        self.cancel_button = self._command_button("取消", QStyle.SP_BrowserStop, self._cancel)
        self.open_button = self._command_button("打开结果", QStyle.SP_DirOpenIcon, self._open_output)
        for button in (self.scan_button, self.start_button, self.pause_button, self.cancel_button):
            commands.addWidget(button)
        commands.addStretch(1)
        commands.addWidget(self.open_button)
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.open_button.setEnabled(False)
        layout.addLayout(commands)

        stats = QHBoxLayout()
        stats.setSpacing(0)
        self.stat_total = self._stat_label("总计", "0")
        self.stat_classified = self._stat_label("已分类", "0")
        self.stat_review = self._stat_label("待复核", "0")
        self.stat_failed = self._stat_label("异常", "0")
        for label in (self.stat_total, self.stat_classified, self.stat_review, self.stat_failed):
            stats.addWidget(label, 1)
        layout.addLayout(stats)

        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索文件名或责任人")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["全部状态"] + [str(status) for status in RecordStatus])
        self.status_filter.currentTextChanged.connect(self._apply_filters)
        filter_row.addWidget(self.search_edit, 1)
        filter_row.addWidget(self.status_filter)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["预览", "文件名", "责任人", "置信度", "引擎", "状态", "旋转", "错误"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(54)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._task_row_activated)
        layout.addWidget(self.table, 1)

        progress_row = QHBoxLayout()
        self.status_label = QLabel("等待选择目录")
        self.status_label.setObjectName("secondaryText")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        progress_row.addWidget(self.status_label)
        progress_row.addWidget(self.progress, 1)
        layout.addLayout(progress_row)
        return page

    def _stat_label(self, title: str, value: str) -> QLabel:
        label = QLabel(f"{title}  {value}")
        label.setObjectName("statLabel")
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumHeight(42)
        return label

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        heading_row = QHBoxLayout()
        header_box = QVBoxLayout()
        heading, subtitle = self._page_header("待复核", "核对低置信度结果并确认责任人")
        header_box.addWidget(heading)
        header_box.addWidget(subtitle)
        heading_row.addLayout(header_box, 1)
        self.review_count = QLabel("0 项")
        self.review_count.setObjectName("countBadge")
        heading_row.addWidget(self.review_count)
        layout.addLayout(heading_row)

        splitter = QSplitter(Qt.Horizontal)
        self.review_table = QTableWidget(0, 3)
        self.review_table.setHorizontalHeaderLabels(["文件", "候选责任人", "置信度"])
        self.review_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.review_table.setSelectionMode(QTableWidget.SingleSelection)
        self.review_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.review_table.verticalHeader().setVisible(False)
        self.review_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.review_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.review_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.review_table.itemSelectionChanged.connect(self._show_review_selection)
        splitter.addWidget(self.review_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(10, 0, 0, 0)
        self.preview = ImagePreview()
        detail_layout.addWidget(self.preview, 3)
        self.review_detail = QLabel("未选择记录")
        self.review_detail.setWordWrap(True)
        detail_layout.addWidget(self.review_detail)
        self.ocr_text = QTextEdit()
        self.ocr_text.setReadOnly(True)
        self.ocr_text.setPlaceholderText("OCR 原文")
        self.ocr_text.setMinimumHeight(120)
        detail_layout.addWidget(self.ocr_text, 1)
        actions = QHBoxLayout()
        self.owner_combo = QComboBox()
        self.owner_combo.setMinimumHeight(36)
        self.owner_combo.setMaxVisibleItems(12)
        self.owner_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.confirm_button = self._command_button("确认分类", QStyle.SP_DialogApplyButton, self._confirm_review, True)
        self.confirm_button.setEnabled(False)
        actions.addWidget(QLabel("责任人"))
        actions.addWidget(self.owner_combo, 1)
        actions.addWidget(self.confirm_button)
        detail_layout.addLayout(actions)
        splitter.addWidget(detail)
        splitter.setSizes([390, 780])
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background:#f4f6f8; color:#202830; font-family:"Microsoft YaHei UI"; font-size:13px; }
            QFrame#sidebar { background:#18242d; border:0; }
            QFrame#sidebar QLabel { background:transparent; }
            QLabel#brand { background:transparent; color:#ffffff; font-size:18px; font-weight:700; padding:3px 6px; }
            QPushButton#navButton { background:transparent; color:#c9d2d9; border:0; border-radius:4px; padding:9px 12px; text-align:left; }
            QPushButton#navButton:hover { background:#233641; color:white; }
            QPushButton#navButton:checked { background:#176b87; color:white; font-weight:600; }
            QPushButton#sidebarVersion { color:#9badb7; background:transparent; border:0; padding:5px 8px; text-align:left; }
            QPushButton#sidebarVersion:hover { color:white; background:#233641; }
            QLabel#pageTitle { font-size:22px; font-weight:700; color:#17212a; }
            QLabel#pageSubtitle, QLabel#secondaryText { color:#66737d; }
            QLabel#statLabel { background:#ffffff; border-top:1px solid #dce2e7; border-bottom:1px solid #dce2e7; color:#31414c; font-weight:600; }
            QLabel#countBadge { background:#fff0c7; color:#7a5500; border-radius:4px; padding:5px 10px; font-weight:600; }
            QScrollArea#imagePreview, QLabel#imageCanvas { background:#151b20; color:#aab5bd; border:0; }
            QScrollArea#imagePreview { border:1px solid #2d3942; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
                background:white; border:1px solid #b9c3ca; border-radius:4px; padding:6px;
                selection-background-color:#176b87;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus { border-color:#176b87; }
            QPushButton { background:#ffffff; border:1px solid #aeb9c1; border-radius:4px; padding:6px 12px; }
            QPushButton:hover { background:#edf3f5; border-color:#176b87; }
            QPushButton:disabled { color:#9ba5ac; background:#eceff1; border-color:#d5dade; }
            QPushButton#primaryButton { background:#176b87; color:white; border-color:#176b87; font-weight:600; }
            QPushButton#primaryButton:hover { background:#12586f; }
            QHeaderView::section { background:#e7ecef; color:#35434d; padding:8px; border:0; border-right:1px solid #cbd3d9; font-weight:600; }
            QTableWidget { background:white; alternate-background-color:#f7f9fa; border:1px solid #cbd3d9; gridline-color:#e2e7ea; }
            QTableWidget::item:selected { background:#d8ebf1; color:#18242d; }
            QProgressBar { background:#dce2e6; border:0; border-radius:3px; height:14px; text-align:center; }
            QProgressBar::chunk { background:#27805f; border-radius:3px; }
            QTabWidget::pane { background:white; border:1px solid #cbd3d9; }
            QTabBar::tab { background:#e7ecef; padding:9px 16px; margin-right:1px; }
            QTabBar::tab:selected { background:white; color:#176b87; font-weight:600; }
            QSplitter::handle { background:#d5dce1; width:1px; }
            """
        )

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if index == 1:
            self._refresh_review_table()
        elif index == 2:
            self.settings_page.reload()

    def _choose_input(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输入目录", self.input_edit.text())
        if directory:
            self.input_edit.setText(native_path_text(directory))
            if not self.output_edit.text():
                self.output_edit.setText(native_path_text(Path(directory).parent))

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择结果保存位置", self.output_edit.text())
        if directory:
            self.output_edit.setText(native_path_text(directory))

    def _scan(self) -> None:
        try:
            input_dir = Path(self.input_edit.text().strip()).expanduser().resolve()
            output_dir = (
                Path(self.output_edit.text().strip()).expanduser().resolve()
                if self.output_edit.text().strip() else input_dir.parent
            )
            self.input_edit.setText(native_path_text(input_dir))
            self.output_edit.setText(native_path_text(output_dir))
            self.images = scan_images(input_dir)
            self.records = [ClassificationRecord(source_path=str(path)) for path in self.images]
            self.current_task_id = None
            self.session_output = None
            self._populate_task_table()
            self.start_button.setEnabled(bool(self.images))
            self.open_button.setEnabled(False)
            self.progress.setValue(0)
            self.status_label.setText("扫描完成" if self.images else "未找到支持的图片")
            self._update_counts()
        except Exception as exc:
            QMessageBox.warning(self, "扫描失败", str(exc))

    def _populate_task_table(self) -> None:
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            self._update_task_row(row, record, load_thumbnail=False)
        self._apply_filters()

    def _update_task_row(self, row: int, record: ClassificationRecord, load_thumbnail: bool = True) -> None:
        preview = QTableWidgetItem()
        preview.setData(Qt.UserRole, row)
        if load_thumbnail:
            pixmap = QPixmap(record.source_path)
            if not pixmap.isNull():
                preview.setIcon(QIcon(pixmap.scaled(64, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        items = [
            preview, QTableWidgetItem(record.file_name),
            QTableWidgetItem(record.owner or record.candidate_owner or "-"),
            QTableWidgetItem(f"{record.confidence:.1%}" if record.confidence else "-"),
            QTableWidgetItem(record.ocr_engine or "-"), QTableWidgetItem(str(record.status)),
            QTableWidgetItem(f"{record.rotation}°"), QTableWidgetItem(record.error),
        ]
        colors = {
            RecordStatus.CLASSIFIED: QColor("#dcefe6"), RecordStatus.REVIEW: QColor("#fff0c7"),
            RecordStatus.UNRECOGNIZED: QColor("#f6dddd"), RecordStatus.FAILED: QColor("#f2cccc"),
        }
        color = colors.get(record.status)
        for column, item in enumerate(items):
            self.table.setItem(row, column, item)
            if color:
                item.setBackground(color)

    def _apply_filters(self) -> None:
        query = self.search_edit.text().strip().lower()
        selected_status = self.status_filter.currentText()
        for row, record in enumerate(self.records):
            text_match = not query or query in f"{record.file_name} {record.owner} {record.candidate_owner}".lower()
            status_match = selected_status == "全部状态" or str(record.status) == selected_status
            self.table.setRowHidden(row, not (text_match and status_match))

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
            self.current_task_id = self.database.create_task(str(Path(self.input_edit.text()).resolve()), str(self.session_output))
            self.records = [ClassificationRecord(source_path=str(path), task_id=self.current_task_id) for path in self.images]
            self._populate_task_table()
            self.settings = self.database.load_settings()
            self.worker = BatchWorker(
                self.images, self.database.owner_alias_map(), self.settings, self.database,
                self.current_task_id, self.session_output, self,
            )
            self.worker.record_ready.connect(self._record_ready)
            self.worker.progress_changed.connect(self._progress_changed)
            self.worker.batch_finished.connect(self._batch_finished)
            self.worker.batch_error.connect(self._batch_error)
            self.worker.start()
            self.scan_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
            self.status_label.setText("正在加载 OCR 模型并识别…")
        except Exception as exc:
            QMessageBox.critical(self, "无法启动", str(exc))

    def _record_ready(self, record: ClassificationRecord, index: int) -> None:
        self.records[index] = record
        self._update_task_row(index, record)
        self._update_counts()

    def _progress_changed(self, current: int, total: int) -> None:
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(current)
        self.status_label.setText(f"正在识别 {current}/{total}")

    def _toggle_pause(self) -> None:
        if not self.worker:
            return
        self.paused = not self.paused
        if self.paused:
            self.worker.pause()
            self.pause_button.setText("继续")
            self.pause_button.setIcon(self._icon(QStyle.SP_MediaPlay))
            self.status_label.setText("任务已暂停")
        else:
            self.worker.resume()
            self.pause_button.setText("暂停")
            self.pause_button.setIcon(self._icon(QStyle.SP_MediaPause))

    def _cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在取消…")

    def _batch_finished(self, success: bool, message: str) -> None:
        self._set_idle_controls()
        self.status_label.setText(message)
        self.open_button.setEnabled(bool(self.session_output))
        self._update_counts()
        self._refresh_review_table()
        if success and any(record.status == RecordStatus.REVIEW for record in self.records):
            self._switch_page(1)

    def _batch_error(self, message: str) -> None:
        self._set_idle_controls()
        self.status_label.setText("任务失败")
        QMessageBox.critical(self, "识别失败", message)

    def _set_idle_controls(self) -> None:
        self.scan_button.setEnabled(True)
        self.start_button.setEnabled(bool(self.images))
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.paused = False
        self.pause_button.setText("暂停")

    def _update_counts(self) -> None:
        counts = Counter(record.status for record in self.records)
        abnormal = counts[RecordStatus.UNRECOGNIZED] + counts[RecordStatus.FAILED]
        self.stat_total.setText(f"总计  {len(self.records)}")
        self.stat_classified.setText(f"已分类  {counts[RecordStatus.CLASSIFIED]}")
        self.stat_review.setText(f"待复核  {counts[RecordStatus.REVIEW]}")
        self.stat_failed.setText(f"异常  {abnormal}")
        pending = counts[RecordStatus.REVIEW] + abnormal
        self.nav_buttons[1].setText(f"待复核  {pending}" if pending else "待复核")
        self.review_count.setText(f"{pending} 项")

    def _review_indexes(self) -> list[int]:
        return [
            index for index, record in enumerate(self.records)
            if record.status in {RecordStatus.REVIEW, RecordStatus.UNRECOGNIZED, RecordStatus.FAILED}
        ]

    def _refresh_review_table(
        self, select_index: int | None = None, select_row: int | None = None,
    ) -> None:
        indexes = self._review_indexes()
        with QSignalBlocker(self.review_table):
            self.review_table.clearSelection()
            self.review_table.setRowCount(len(indexes))
            selected_row = -1
            for row, record_index in enumerate(indexes):
                record = self.records[record_index]
                file_item = QTableWidgetItem(record.file_name)
                file_item.setData(Qt.UserRole, record_index)
                self.review_table.setItem(row, 0, file_item)
                self.review_table.setItem(row, 1, QTableWidgetItem(record.candidate_owner or "未识别"))
                self.review_table.setItem(row, 2, QTableWidgetItem(f"{record.confidence:.1%}"))
                if select_index == record_index:
                    selected_row = row
            if indexes:
                if selected_row < 0:
                    selected_row = min(select_row if select_row is not None else 0, len(indexes) - 1)
                self.review_table.selectRow(selected_row)
        self._update_counts()
        if indexes:
            self._show_review_selection()
        else:
            self.preview.set_record(None)
            self.review_detail.setText("没有待复核记录")
            self.ocr_text.clear()
            self.confirm_button.setEnabled(False)

    def _selected_review_index(self) -> int | None:
        row = self.review_table.currentRow()
        item = self.review_table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.UserRole)) if item else None

    def _show_review_selection(self) -> None:
        index = self._selected_review_index()
        if index is None:
            return
        record = self.records[index]
        self.preview.set_record(record)
        self.ocr_text.setPlainText(record.ocr_text)
        self.review_detail.setText(
            f"{record.file_name}\n候选：{record.candidate_owner or '无'}   "
            f"置信度：{record.confidence:.1%}   状态：{record.status}"
            + (f"\n{record.error}" if record.error else "")
        )
        owners = self.database.owners()
        self.owner_combo.clear()
        self.owner_combo.addItems(owners)
        target = record.owner or record.candidate_owner
        if target in owners:
            self.owner_combo.setCurrentText(target)
        self.confirm_button.setEnabled(bool(owners) and not (self.worker and self.worker.isRunning()))

    def _confirm_review(self) -> None:
        index = self._selected_review_index()
        if index is None or not self.session_output or self.current_task_id is None:
            return
        owner = self.owner_combo.currentText().strip()
        if not owner:
            QMessageBox.warning(self, "无法确认", "请选择责任人。")
            return
        try:
            review_row = self.review_table.currentRow()
            record = self.records[index]
            old_output = Path(record.output_path) if record.output_path else None
            record.owner = owner
            record.reviewed = True
            record.status = RecordStatus.CONFIRMED
            ClassificationService(self.session_output, self.database.load_settings()).classify(record, owner)
            if old_output and old_output.parent.name == "未识别" and old_output.exists():
                old_output.unlink()
            self.database.save_record(record)
            self._update_task_row(index, record)
            self._refresh_review_table(select_row=review_row)
            self._update_counts()
            if not any(item.status == RecordStatus.REVIEW for item in self.records):
                self.database.update_task_status(self.current_task_id, "已完成")
        except Exception as exc:
            QMessageBox.critical(self, "分类失败", str(exc))

    def _task_row_activated(self, row: int, column: int) -> None:
        if 0 <= row < len(self.records) and self.records[row].status in {
            RecordStatus.REVIEW, RecordStatus.UNRECOGNIZED, RecordStatus.FAILED
        }:
            self._switch_page(1)
            self._refresh_review_table(select_index=row)

    def _open_output(self) -> None:
        if self.session_output and self.session_output.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session_output)))

    def _settings_saved(self) -> None:
        self.settings = self.database.load_settings()
        self.status_label.setText("设置已自动保存")

    def _owners_changed(self) -> None:
        owners = self.database.owners()
        current = self.owner_combo.currentText()
        selected = self._selected_review_index()
        candidate = self.records[selected].candidate_owner if selected is not None else ""
        with QSignalBlocker(self.owner_combo):
            self.owner_combo.clear()
            self.owner_combo.addItems(owners)
            target = current if current in owners else candidate
            if target in owners:
                self.owner_combo.setCurrentText(target)
        self.confirm_button.setEnabled(bool(owners) and selected is not None)
        self.status_label.setText("责任人名单已更新")

    def _open_update_settings(self) -> None:
        self._switch_page(2)
        self.settings_page.show_update_tab(check=True)

    def _update_available(self, version: str) -> None:
        self.version_button.setText(f"版本 {__version__}\n可更新至 {version}")
        self.version_button.setStyleSheet("color:#ffd27a; font-weight:600;")

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self.database.close()
        event.accept()


def main() -> int:
    if "--smoke-ocr" in sys.argv:
        try:
            index = sys.argv.index("--smoke-ocr")
            image_path = Path(sys.argv[index + 1])
            from .database import DEFAULT_OWNERS
            from .engine import RecognitionEngine
            from .models import AppSettings
            from .ocr import create_local_provider
            record = RecognitionEngine(
                create_local_provider(prefer_paddle=False), DEFAULT_OWNERS, AppSettings()
            ).classify(image_path)
            return 0 if record.candidate_owner else 2
        except Exception:
            return 3
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ConstructionOwnerClassifier")
    app.setStyle("Fusion")
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    icon_path = resource_root / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    instance_guard = SingleInstanceGuard(INSTANCE_MUTEX_NAME)
    if not instance_guard.acquire():
        QMessageBox.information(None, "程序已在运行", "施工责任人图片分类器已经在运行，请勿重复打开。")
        return 0
    try:
        window = MainWindow()
        window.show()
        return app.exec()
    finally:
        instance_guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
