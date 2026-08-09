from __future__ import annotations

import csv
import re

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import __version__
from .database import Database
from .models import AppSettings
from .updater import UpdateInfo, UpdateService


def _split_aliases(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in re.split(r"[,，;；|/\n]+", value) if part.strip()))


class UpdateCheckWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: UpdateService, parent=None) -> None:
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        try:
            self.completed.emit(self.service.check_for_update(__version__))
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, service: UpdateService, info: UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.info = info

    def run(self) -> None:
        try:
            path = self.service.download(self.info, self.progress.emit)
            self.completed.emit(str(path))
        except Exception as exc:
            self.failed.emit(str(exc))


class OwnerEditDialog(QDialog):
    def __init__(self, title: str, name: str = "", aliases: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("真实姓名，例如：曹华兵")
        self.aliases_edit = QTextEdit()
        self.aliases_edit.setPlaceholderText("水印错别字或其他写法，一行一个，例如：曹华斌")
        self.aliases_edit.setMaximumHeight(120)
        self.aliases_edit.setPlainText("\n".join(aliases or []))
        form.addRow("责任人", self.name_edit)
        form.addRow("识别别名", self.aliases_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, list[str]]:
        return self.name_edit.text().strip(), _split_aliases(self.aliases_edit.toPlainText())


class SettingsPage(QWidget):
    settings_saved = Signal()
    owners_changed = Signal()
    update_available = Signal(str)

    def __init__(self, database: Database, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.settings = database.load_settings()
        self.update_service = UpdateService()
        self.update_check_worker: UpdateCheckWorker | None = None
        self.update_download_worker: UpdateDownloadWorker | None = None
        self.available_update: UpdateInfo | None = None
        self.downloaded_installer = ""
        self._loading_settings = False
        self._keyword_save_timer = QTimer(self)
        self._keyword_save_timer.setSingleShot(True)
        self._keyword_save_timer.setInterval(350)
        self._keyword_save_timer.timeout.connect(self._save_settings)
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 22)
        root.setSpacing(14)
        heading = QLabel("设置")
        heading.setObjectName("pageTitle")
        root.addWidget(heading)
        subtitle = QLabel("更改会自动保存并立即用于后续识别")
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._owner_tab(), "责任人名单")
        self.tabs.addTab(self._recognition_tab(), "识别设置")
        self.update_tab = self._update_tab()
        self.tabs.addTab(self.update_tab, "软件更新")
        root.addWidget(self.tabs, 1)

    def _owner_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 16, 14, 14)
        self.owner_table = QTableWidget(0, 4)
        self.owner_table.setHorizontalHeaderLabels(["编号", "责任人", "识别别名", "操作"])
        self.owner_table.setColumnHidden(0, True)
        self.owner_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.owner_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.owner_table.verticalHeader().setVisible(False)
        header = self.owner_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.owner_table)

        controls = QHBoxLayout()
        self.new_owner = QLineEdit()
        self.new_owner.setPlaceholderText("责任人真实姓名")
        self.new_aliases = QLineEdit()
        self.new_aliases.setPlaceholderText("识别别名，可用逗号或分号分隔")
        self.new_owner.returnPressed.connect(self._add_owner)
        self.new_aliases.returnPressed.connect(self._add_owner)
        add_button = QPushButton("添加")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_owner)
        import_button = QPushButton("导入 CSV")
        import_button.clicked.connect(self._import_csv)
        controls.addWidget(self.new_owner, 1)
        controls.addWidget(self.new_aliases, 2)
        controls.addWidget(add_button)
        controls.addWidget(import_button)
        layout.addLayout(controls)
        return widget

    def _recognition_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(24, 24, 24, 24)
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(16)
        self.auto_threshold = QDoubleSpinBox()
        self.auto_threshold.setRange(0.50, 1.00)
        self.auto_threshold.setSingleStep(0.01)
        self.review_threshold = QDoubleSpinBox()
        self.review_threshold.setRange(0.10, 0.95)
        self.review_threshold.setSingleStep(0.01)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 4)
        self.duplicate_policy = QComboBox()
        self.duplicate_policy.addItems(["重命名", "跳过", "覆盖"])
        self.file_operation = QComboBox()
        self.file_operation.addItems(["复制", "移动"])
        self.recognition_keywords = QTextEdit()
        self.recognition_keywords.setMaximumHeight(120)
        self.recognition_keywords.setPlaceholderText("每行一个字段关键词")
        form.addRow("自动分类阈值", self.auto_threshold)
        form.addRow("人工复核阈值", self.review_threshold)
        form.addRow("并发识别数", self.concurrency)
        form.addRow("同名文件策略", self.duplicate_policy)
        form.addRow("识别文件策略", self.file_operation)
        form.addRow("识别关键词", self.recognition_keywords)
        note = QLabel("移动模式会先复制并校验目标文件，确认完整后才删除原图；未识别图片会移动到“未识别”目录。")
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        form.addRow("", note)

        self.auto_threshold.valueChanged.connect(self._save_settings)
        self.review_threshold.valueChanged.connect(self._save_settings)
        self.concurrency.valueChanged.connect(self._save_settings)
        self.duplicate_policy.currentTextChanged.connect(self._save_settings)
        self.file_operation.currentTextChanged.connect(self._save_settings)
        self.recognition_keywords.textChanged.connect(lambda: self._keyword_save_timer.start())
        return widget

    def _update_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        form = QFormLayout()
        form.addRow("当前版本", QLabel(__version__))
        form.addRow("发布渠道", QLabel("GitHub Releases · DPeak0/ConstructionOwnerClassifier"))
        self.update_auto_check = QCheckBox("启动后自动检查更新")
        self.update_auto_check.toggled.connect(self._save_settings)
        form.addRow("自动检查", self.update_auto_check)
        layout.addLayout(form)
        self.update_status = QLabel("尚未检查更新")
        self.update_status.setObjectName("secondaryText")
        self.update_status.setWordWrap(True)
        layout.addWidget(self.update_status)
        self.update_notes = QTextEdit()
        self.update_notes.setReadOnly(True)
        self.update_notes.setPlaceholderText("版本说明")
        self.update_notes.setMaximumHeight(150)
        self.update_notes.hide()
        layout.addWidget(self.update_notes)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.hide()
        layout.addWidget(self.update_progress)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.check_update_button = QPushButton("检查更新")
        self.check_update_button.clicked.connect(self.check_for_updates)
        self.download_update_button = QPushButton("下载更新")
        self.download_update_button.setObjectName("primaryButton")
        self.download_update_button.clicked.connect(self._download_update)
        self.download_update_button.hide()
        actions.addWidget(self.check_update_button)
        actions.addWidget(self.download_update_button)
        layout.addLayout(actions)
        layout.addStretch(1)
        return widget

    def reload(self) -> None:
        self._loading_settings = True
        self.settings = self.database.load_settings()
        self._reload_owners()
        self.auto_threshold.setValue(self.settings.auto_threshold)
        self.review_threshold.setValue(self.settings.review_threshold)
        self.concurrency.setValue(self.settings.concurrency)
        self.duplicate_policy.setCurrentText(self.settings.duplicate_policy)
        self.file_operation.setCurrentText(self.settings.file_operation)
        self.recognition_keywords.setPlainText("\n".join(self.settings.recognition_keywords))
        self.update_auto_check.setChecked(self.settings.update_auto_check)
        self._loading_settings = False

    def _reload_owners(self) -> None:
        rows = self.database.owner_rows()
        self.owner_table.setRowCount(len(rows))
        for row, (owner_id, name, aliases) in enumerate(rows):
            self.owner_table.setItem(row, 0, QTableWidgetItem(str(owner_id)))
            self.owner_table.setItem(row, 1, QTableWidgetItem(name))
            self.owner_table.setItem(row, 2, QTableWidgetItem("、".join(aliases) or "-"))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(3, 2, 3, 2)
            action_layout.setSpacing(6)
            modify = QPushButton("修改")
            delete = QPushButton("删除")
            modify.clicked.connect(lambda checked=False, value=owner_id: self._edit_owner(value))
            delete.clicked.connect(lambda checked=False, value=owner_id: self._delete_owner(value))
            action_layout.addWidget(modify)
            action_layout.addWidget(delete)
            self.owner_table.setCellWidget(row, 3, actions)

    def _add_owner(self) -> None:
        try:
            self.database.add_owner(self.new_owner.text(), _split_aliases(self.new_aliases.text()))
            self.new_owner.clear()
            self.new_aliases.clear()
            self._reload_owners()
            self.owners_changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "无法添加", str(exc))

    def _edit_owner(self, owner_id: int) -> None:
        row = next((item for item in self.database.owner_rows() if item[0] == owner_id), None)
        if row is None:
            return
        dialog = OwnerEditDialog("修改责任人", row[1], row[2], self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.database.update_owner(owner_id, *dialog.values())
            self._reload_owners()
            self.owners_changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "无法修改", str(exc))

    def _delete_owner(self, owner_id: int) -> None:
        row = next((item for item in self.database.owner_rows() if item[0] == owner_id), None)
        if row is None:
            return
        if QMessageBox.question(
            self, "删除责任人", f"确定删除“{row[1]}”及其全部识别别名吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.database.delete_owner(owner_id)
        self._reload_owners()
        self.owners_changed.emit()

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入责任人名单", "", "CSV 文件 (*.csv)")
        if not path:
            return
        added = 0
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as stream:
                for row in csv.reader(stream):
                    if not row or row[0].strip() in {"责任人", "姓名", "name"}:
                        continue
                    aliases = _split_aliases(row[1]) if len(row) > 1 else []
                    self.database.add_owner(row[0], aliases)
                    added += 1
            self._reload_owners()
            self.owners_changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"已添加 {added} 人。\n{exc}")

    def _settings_from_form(self) -> AppSettings:
        keywords = _split_aliases(self.recognition_keywords.toPlainText())
        return AppSettings(
            auto_threshold=self.auto_threshold.value(),
            review_threshold=self.review_threshold.value(),
            concurrency=self.concurrency.value(),
            duplicate_policy=self.duplicate_policy.currentText(),
            file_operation=self.file_operation.currentText(),
            recognition_keywords=keywords,
            update_auto_check=self.update_auto_check.isChecked(),
        )

    def _save_settings(self, *_args) -> None:
        if self._loading_settings:
            return
        settings = self._settings_from_form()
        if settings.review_threshold >= settings.auto_threshold:
            changed = self.sender()
            self._loading_settings = True
            if changed is self.auto_threshold:
                self.review_threshold.setValue(max(0.10, settings.auto_threshold - 0.01))
            else:
                self.auto_threshold.setValue(min(1.0, settings.review_threshold + 0.01))
            self._loading_settings = False
            settings = self._settings_from_form()
        if not settings.recognition_keywords:
            return
        self.database.save_settings(settings)
        self.settings = settings
        self.settings_saved.emit()

    def show_update_tab(self, check: bool = False) -> None:
        self.tabs.setCurrentWidget(self.update_tab)
        if check:
            self.check_for_updates()

    def check_for_updates(self, silent: bool = False) -> None:
        if self.update_check_worker and self.update_check_worker.isRunning():
            return
        self.check_update_button.setEnabled(False)
        self.update_status.setText("正在连接更新服务器…")
        self.update_check_worker = UpdateCheckWorker(self.update_service, self)
        self.update_check_worker.completed.connect(self._update_checked)
        self.update_check_worker.failed.connect(lambda message: self._update_check_failed(message, silent))
        self.update_check_worker.start()

    def _update_checked(self, info: UpdateInfo | None) -> None:
        self.check_update_button.setEnabled(True)
        self.available_update = info
        if info is None:
            self.update_status.setText(f"当前已是最新版本 {__version__}")
            self.download_update_button.hide()
            self.update_notes.hide()
            return
        self.update_status.setText(f"发现新版本 {info.version}，安装包 {info.size / 1024 / 1024:.1f} MB")
        self.update_notes.setPlainText(info.notes or "本版本未提供说明。")
        self.update_notes.show()
        self.download_update_button.setText("下载并安装")
        self.download_update_button.show()
        self.update_available.emit(info.version)

    def _update_check_failed(self, message: str, silent: bool) -> None:
        self.check_update_button.setEnabled(True)
        self.update_status.setText(message)
        if not silent:
            QMessageBox.warning(self, "检查更新失败", message)

    def _download_update(self) -> None:
        if not self.available_update or (self.update_download_worker and self.update_download_worker.isRunning()):
            return
        self.download_update_button.setEnabled(False)
        self.check_update_button.setEnabled(False)
        self.update_progress.setValue(0)
        self.update_progress.show()
        self.update_status.setText("正在下载更新…")
        self.update_download_worker = UpdateDownloadWorker(self.update_service, self.available_update, self)
        self.update_download_worker.progress.connect(self._update_download_progress)
        self.update_download_worker.completed.connect(self._update_downloaded)
        self.update_download_worker.failed.connect(self._update_download_failed)
        self.update_download_worker.start()

    def _update_download_progress(self, current: int, total: int) -> None:
        self.update_progress.setValue(min(100, int(current * 100 / max(total, 1))))
        self.update_status.setText(f"正在下载更新 {current / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB")

    def _update_downloaded(self, path: str) -> None:
        self.downloaded_installer = path
        self.update_progress.setValue(100)
        self.update_status.setText("更新已下载并通过完整性校验")
        self.download_update_button.setEnabled(True)
        self.check_update_button.setEnabled(True)
        if QMessageBox.question(
            self, "安装更新", "更新已准备完成。现在关闭软件并启动安装程序吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            try:
                self.update_service.launch_installer(path)
                QApplication.quit()
            except Exception as exc:
                QMessageBox.warning(self, "无法安装更新", str(exc))

    def _update_download_failed(self, message: str) -> None:
        self.update_status.setText(message)
        self.download_update_button.setEnabled(True)
        self.check_update_button.setEnabled(True)
        QMessageBox.warning(self, "下载更新失败", message)
