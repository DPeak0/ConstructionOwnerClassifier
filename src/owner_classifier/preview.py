from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .models import ClassificationRecord


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
        viewport_position = (
            self.mapTo(viewport, event.position().toPoint())
            if viewport else event.position().toPoint()
        )
        self.zoom_requested.emit(
            1 if event.angleDelta().y() > 0 else -1,
            event.position(),
            QPointF(viewport_position),
        )
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            viewport = self.parentWidget()
            viewport_position = (
                self.mapTo(viewport, event.position().toPoint())
                if viewport else event.position().toPoint()
            )
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
        self.setMinimumSize(360, 280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self._base_source = QPixmap()
        self._source = QPixmap()
        self._scale = 1.0
        self._fit_mode = True
        self._view_rotation = 0
        self.current_source = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch(1)
        self.zoom_out_button = self._tool_button("−", "缩小图片", self.zoom_out)
        self.actual_button = self._tool_button("1:1", "显示原始大小", self.actual_size)
        self.fit_button = self._tool_button("适应", "适应预览窗口", self.fit_to_window)
        self.zoom_in_button = self._tool_button("+", "放大图片", self.zoom_in)
        self.rotate_button = self._tool_button("↻", "顺时针旋转 90°", self.rotate_clockwise)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        for widget in (
            self.zoom_out_button,
            self.actual_button,
            self.fit_button,
            self.zoom_in_button,
            self.rotate_button,
            self.zoom_label,
        ):
            toolbar.addWidget(widget)
        layout.addLayout(toolbar)

        self.canvas = PreviewCanvas("选择任务记录")
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
                (
                    path
                    for path in (
                        Path(record.source_path),
                        Path(record.output_path) if record.output_path else None,
                    )
                    if path is not None and path.is_file()
                ),
                None,
            )
        if record is None or image_path is None:
            self._clear("图片不可用" if record else "选择任务记录")
            return

        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            self._clear("无法加载图片")
            return
        if record.rotation:
            pixmap = pixmap.transformed(
                QTransform().rotate(-record.rotation), Qt.SmoothTransformation
            )
        try:
            blocks = json.loads(record.ocr_blocks or "[]")
        except json.JSONDecodeError:
            blocks = []
        if blocks:
            painted = QPixmap(pixmap)
            painter = QPainter(painted)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#ef4444"), max(2, painted.width() // 700)))
            for block in blocks:
                points = block.get("box", [])
                if len(points) >= 4:
                    painter.drawPolygon(
                        QPolygonF([QPointF(float(x), float(y)) for x, y in points])
                    )
            painter.end()
            pixmap = painted
        self._base_source = pixmap
        self._source = pixmap
        self._view_rotation = 0
        self.current_source = str(image_path)
        self.fit_to_window()

    def _clear(self, message: str) -> None:
        self._base_source = QPixmap()
        self._source = QPixmap()
        self._view_rotation = 0
        self.current_source = ""
        self.canvas.clear()
        self.canvas.setText(message)
        self.canvas.resize(self.scroll.viewport().size())
        self.zoom_label.setText("—")

    @property
    def scale_factor(self) -> float:
        return self._scale

    @property
    def view_rotation(self) -> int:
        return self._view_rotation

    def zoom_in(self) -> None:
        self._set_scale(self._scale * 1.25)

    def zoom_out(self) -> None:
        self._set_scale(self._scale / 1.25)

    def actual_size(self) -> None:
        self._set_scale(1.0)

    def rotate_clockwise(self) -> None:
        if self._base_source.isNull():
            return
        self._view_rotation = (self._view_rotation + 90) % 360
        self._source = self._base_source.transformed(
            QTransform().rotate(self._view_rotation), Qt.SmoothTransformation
        )
        self.fit_to_window()

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

    def _wheel_zoom(
        self, direction: int, image_position: QPointF, viewport_position: QPointF
    ) -> None:
        factor = 1.15 if direction > 0 else 1 / 1.15
        self._set_scale(self._scale * factor, image_position, viewport_position)

    def _double_click_zoom(
        self, image_position: QPointF, viewport_position: QPointF
    ) -> None:
        self._set_scale(self._scale * 2.0, image_position, viewport_position)

    def _pan(self, delta: QPoint) -> None:
        horizontal = self.scroll.horizontalScrollBar()
        vertical = self.scroll.verticalScrollBar()
        horizontal.setValue(horizontal.value() - delta.x())
        vertical.setValue(vertical.value() - delta.y())

    def _set_scale(
        self,
        scale: float,
        image_position: QPointF | None = None,
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
        center_x = (horizontal.value() + horizontal.pageStep() / 2) / max(
            horizontal.maximum() + horizontal.pageStep(), 1
        )
        center_y = (vertical.value() + vertical.pageStep() / 2) / max(
            vertical.maximum() + vertical.pageStep(), 1
        )
        size = self._source.size() * self._scale
        pixmap = self._source.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.canvas.setPixmap(pixmap)
        self.canvas.resize(pixmap.size())
        self.zoom_label.setText(f"{self._scale:.0%}")
        if preserve_center:
            horizontal.setValue(
                int(
                    center_x * (horizontal.maximum() + horizontal.pageStep())
                    - horizontal.pageStep() / 2
                )
            )
            vertical.setValue(
                int(
                    center_y * (vertical.maximum() + vertical.pageStep())
                    - vertical.pageStep() / 2
                )
            )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            QTimer.singleShot(0, self.fit_to_window)
