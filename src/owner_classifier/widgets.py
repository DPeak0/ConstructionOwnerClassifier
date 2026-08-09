from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Wraps compact controls onto additional rows as width decreases."""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout_items(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout_items(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout_items(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            widget = item.widget()
            style = widget.style() if widget is not None else None
            horizontal_spacing = spacing
            vertical_spacing = spacing
            if spacing < 0 and style is not None:
                horizontal_spacing = style.layoutSpacing(
                    QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal
                )
                vertical_spacing = style.layoutSpacing(
                    QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Vertical
                )
            next_x = x + item.sizeHint().width() + horizontal_spacing
            if next_x - horizontal_spacing > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + vertical_spacing
                next_x = x + item.sizeHint().width() + horizontal_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + margins.bottom()


class FlowWidget(QWidget):
    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self.flow_layout = FlowLayout(self, spacing=spacing)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def refreshHeight(self) -> None:
        width = max(self.contentsRect().width(), 1)
        height = max(self.flow_layout.heightForWidth(width), 1)
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refreshHeight()


class AnchoredComboBox(QComboBox):
    """Positions the popup outside the editor instead of covering it."""

    def showPopup(self) -> None:
        super().showPopup()
        QTimer.singleShot(0, self._position_popup)

    def _position_popup(self) -> None:
        popup = self.view().window()
        anchor = self.mapToGlobal(QPoint(0, self.height() + 2))
        screen = QGuiApplication.screenAt(anchor) or self.screen()
        available = screen.availableGeometry()

        width = max(self.width(), popup.width())
        height = popup.height()
        x = min(max(anchor.x(), available.left()), available.right() - width + 1)
        room_below = available.bottom() - anchor.y() + 1
        if height <= room_below:
            y = anchor.y()
        else:
            above = self.mapToGlobal(QPoint(0, 0)).y() - height - 2
            y = max(available.top(), above)
        popup.setGeometry(x, y, width, height)
