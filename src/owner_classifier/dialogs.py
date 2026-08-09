from __future__ import annotations

import csv
import re
import threading

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from . import __version__
from .database import Database
from .credentials import clear_api_key, load_api_key, save_api_key
from .models import AppSettings
from .performance import recommended_concurrency
from .updater import UpdateCancelled, UpdateInfo, UpdateService
from .widgets import AnchoredComboBox


def _split_aliases(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in re.split(r"[,，;；|/\n]+", value) if part.strip()))


def _split_owner_names(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in re.split(r"[,，;；\r\n]+", value) if part.strip()))


class UpdateCheckWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, service: UpdateService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self.requestInterruption()

    def run(self) -> None:
        try:
            result = self.service.check_for_update(
                __version__, cancelled=self._cancelled.is_set
            )
            if not self._cancelled.is_set():
                self.completed.emit(result)
        except UpdateCancelled:
            return
        except Exception as exc:
            if not self._cancelled.is_set():
                self.failed.emit(str(exc))


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, service: UpdateService, info: UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.info = info
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self.requestInterruption()

    def run(self) -> None:
        try:
            path = self.service.download(
                self.info,
                self.progress.emit,
                cancelled=self._cancelled.is_set,
            )
            if not self._cancelled.is_set():
                self.completed.emit(str(path))
        except UpdateCancelled:
            return
        except Exception as exc:
            if not self._cancelled.is_set():
                self.failed.emit(str(exc))


class AiConnectionWorker(QThread):
    completed = Signal(bool, str)

    def __init__(self, api_key: str, model: str, timeout: int, parent=None) -> None:
        super().__init__(parent)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            from .ai import GlmVisionReviewer

            ok, message = GlmVisionReviewer(
                self.api_key, self.model, self.timeout
            ).test_connection()
            if not self.isInterruptionRequested():
                self.completed.emit(ok, message)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.completed.emit(False, str(exc))


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
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.confirm_button = QPushButton("确定")
        self.confirm_button.setObjectName("primaryButton")
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)

    def values(self) -> tuple[str, list[str]]:
        return self.name_edit.text().strip(), _split_aliases(self.aliases_edit.toPlainText())


class SettingsPage(QWidget):
    settings_saved = Signal()
    owners_changed = Signal()
    update_available = Signal(str)

    def __init__(self, database: Database, parent=None, show_header: bool = True) -> None:
        super().__init__(parent)
        self.database = database
        self.show_header = show_header
        self.settings = database.load_settings()
        self.update_service = UpdateService()
        self.update_check_worker: UpdateCheckWorker | None = None
        self.update_download_worker: UpdateDownloadWorker | None = None
        self.available_update: UpdateInfo | None = None
        self.downloaded_installer = ""
        self.ai_connection_worker: AiConnectionWorker | None = None
        self._ai_key_validated = False
        self._pending_api_key = ""
        self._stored_api_key = ""
        self._loading_settings = False
        self._keyword_save_timer = QTimer(self)
        self._keyword_save_timer.setSingleShot(True)
        self._keyword_save_timer.setInterval(350)
        self._keyword_save_timer.timeout.connect(self._save_settings)
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        if self.show_header:
            heading = QLabel("设置")
            heading.setObjectName("pageTitle")
            root.addWidget(heading)
            subtitle = QLabel("更改会自动保存并立即用于后续识别")
            subtitle.setObjectName("pageSubtitle")
            root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._owner_tab(), "责任人名单")
        self.tabs.addTab(self._recognition_tab(), "识别设置")
        self.update_tab = self._update_tab()
        self.tabs.addTab(self.update_tab, "软件更新")
        self.ai_tab = self._ai_tab()
        self.tabs.addTab(self.ai_tab, "AI增强")
        root.addWidget(self.tabs, 1)

    def _owner_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 16, 14, 14)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.new_owner = QLineEdit()
        self.new_owner.setPlaceholderText("责任人姓名，多个姓名用逗号分隔")
        self.new_owner.setClearButtonEnabled(True)
        self.new_owner.returnPressed.connect(self._add_owner)
        add_button = QPushButton("添加")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_owner)
        import_button = QPushButton("导入 CSV")
        import_button.clicked.connect(self._import_csv)
        controls.addWidget(self.new_owner, 1)
        controls.addWidget(add_button)
        controls.addWidget(import_button)
        layout.addLayout(controls)

        self.owner_table = QTableWidget(0, 4)
        self.owner_table.setHorizontalHeaderLabels(["编号", "责任人", "识别别名", "操作"])
        self.owner_table.setColumnHidden(0, True)
        self.owner_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.owner_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.owner_table.setAlternatingRowColors(True)
        self.owner_table.setShowGrid(False)
        self.owner_table.verticalHeader().setVisible(False)
        self.owner_table.verticalHeader().setDefaultSectionSize(40)
        header = self.owner_table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.owner_table.setColumnWidth(3, 154)
        layout.addWidget(self.owner_table, 1)
        return widget

    def _recognition_tab(self) -> QWidget:
        widget = QWidget()
        root = QVBoxLayout(widget)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)
        self.auto_threshold = QDoubleSpinBox()
        self.auto_threshold.setRange(0.50, 1.00)
        self.auto_threshold.setSingleStep(0.01)
        self.auto_threshold.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.review_threshold = QDoubleSpinBox()
        self.review_threshold.setRange(0.10, 0.95)
        self.review_threshold.setSingleStep(0.01)
        self.review_threshold.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.recommended_concurrency = recommended_concurrency()
        self.concurrency = AnchoredComboBox()
        self.concurrency.addItem(f"自动（推荐 {self.recommended_concurrency}）", 0)
        for count in range(1, 5):
            self.concurrency.addItem(f"手动 {count}", count)
        self.duplicate_policy = AnchoredComboBox()
        self.duplicate_policy.addItems(["重命名", "跳过", "覆盖"])
        self.file_operation = AnchoredComboBox()
        self.file_operation.addItems(["复制", "移动"])
        self.recognition_keywords = QTextEdit()
        self.recognition_keywords.setMaximumHeight(120)
        self.recognition_keywords.setPlaceholderText("每行一个字段关键词")
        for control in (
            self.auto_threshold,
            self.review_threshold,
            self.concurrency,
            self.duplicate_policy,
            self.file_operation,
        ):
            control.setMinimumWidth(210)
            control.setMaximumWidth(300)

        settings_grid = QGridLayout()
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setHorizontalSpacing(36)
        settings_grid.setVerticalSpacing(0)
        settings_grid.setColumnStretch(0, 1)
        settings_grid.setColumnStretch(1, 1)

        decision_group = QWidget()
        decision_layout = QVBoxLayout(decision_group)
        decision_layout.setContentsMargins(0, 0, 0, 0)
        decision_layout.setSpacing(10)
        decision_heading = QLabel("识别判定")
        decision_heading.setObjectName("sectionTitle")
        decision_layout.addWidget(decision_heading)
        decision_form = QFormLayout()
        decision_form.setContentsMargins(0, 0, 0, 0)
        decision_form.setHorizontalSpacing(18)
        decision_form.setVerticalSpacing(14)
        decision_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        decision_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        decision_form.addRow("自动分类阈值", self.auto_threshold)
        decision_form.addRow("人工复核阈值", self.review_threshold)
        decision_form.addRow("并发识别数", self.concurrency)
        decision_layout.addLayout(decision_form)

        file_group = QWidget()
        file_layout = QVBoxLayout(file_group)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(10)
        file_heading = QLabel("文件处理")
        file_heading.setObjectName("sectionTitle")
        file_layout.addWidget(file_heading)
        file_form = QFormLayout()
        file_form.setContentsMargins(0, 0, 0, 0)
        file_form.setHorizontalSpacing(18)
        file_form.setVerticalSpacing(14)
        file_form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        file_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        file_form.addRow("同名文件策略", self.duplicate_policy)
        file_form.addRow("识别文件策略", self.file_operation)
        file_layout.addLayout(file_form)
        file_layout.addStretch(1)

        settings_grid.addWidget(decision_group, 0, 0)
        settings_grid.addWidget(file_group, 0, 1)
        root.addLayout(settings_grid)

        keyword_heading = QLabel("字段关键词")
        keyword_heading.setObjectName("sectionTitle")
        root.addWidget(keyword_heading)
        root.addWidget(self.recognition_keywords)
        note = QLabel("移动模式会先复制并校验目标文件，确认完整后才删除原图；未识别图片会移动到“未识别”目录。")
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch(1)

        self.auto_threshold.valueChanged.connect(self._save_settings)
        self.review_threshold.valueChanged.connect(self._save_settings)
        self.concurrency.currentIndexChanged.connect(self._save_settings)
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
        form.setHorizontalSpacing(28)
        form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        form.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
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

    def _ai_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        heading = QLabel("OCR 语义辅助判断")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(14)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ai_enabled = QCheckBox("启用 AI 增强")
        self.ai_provider = AnchoredComboBox()
        self.ai_provider.addItem("智谱 AI", "zhipu")
        self.ai_model = AnchoredComboBox()
        self.ai_model.addItem("GLM-4.6V-Flash", "glm-4.6v-flash")
        self.ai_api_key = QLineEdit()
        self.ai_api_key.setEchoMode(QLineEdit.Password)
        self.ai_api_key.setPlaceholderText("填写智谱 API Key")
        self.ai_api_key.setClearButtonEnabled(False)
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(self.ai_api_key, 1)
        self.ai_show_key = QPushButton("显示")
        self.ai_show_key.setCheckable(True)
        self.ai_show_key.setToolTip("显示或隐藏 API Key")
        self.ai_show_key.setFixedWidth(58)
        self.ai_clear_key = QPushButton("清除")
        self.ai_clear_key.setToolTip("清除本机保存的 API Key")
        self.ai_clear_key.setFixedWidth(58)
        key_layout.addWidget(self.ai_show_key)
        key_layout.addWidget(self.ai_clear_key)
        form.addRow("启用状态", self.ai_enabled)
        form.addRow("服务商", self.ai_provider)
        form.addRow("模型", self.ai_model)
        form.addRow("API Key", key_row)
        layout.addLayout(form)

        self.ai_status = QLabel("AI 增强未启用")
        self.ai_status.setObjectName("secondaryText")
        self.ai_status.setWordWrap(True)
        layout.addWidget(self.ai_status)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.ai_test_button = QPushButton("测试连接")
        self.ai_test_button.setObjectName("primaryButton")
        actions.addWidget(self.ai_test_button)
        layout.addLayout(actions)

        links = QLabel(
            '<a href="https://open.bigmodel.cn">注册 / 登录</a>　'
            '<a href="https://bigmodel.cn/usercenter/proj-mgmt/overview">打开控制台</a>　'
            '<a href="https://bigmodel.cn/usercenter/proj-mgmt/apikeys">创建 API Key</a>'
        )
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(Qt.TextBrowserInteraction)
        layout.addWidget(links)
        privacy = QLabel(
            "启用后仅会将低置信度结果的 OCR 文字、坐标摘要和本地候选发送至智谱 AI；"
            "本地 OCR 无法识别责任人时，会上传压缩后的疑似水印区域或整图做兜底识别。"
            "API Key 使用 Windows 当前用户 DPAPI 加密保存。"
        )
        privacy.setObjectName("secondaryText")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        layout.addStretch(1)

        self.ai_enabled.toggled.connect(self._ai_enabled_changed)
        self.ai_api_key.textEdited.connect(self._api_key_edited)
        self.ai_provider.currentIndexChanged.connect(self._save_settings)
        self.ai_model.currentIndexChanged.connect(self._save_settings)
        self.ai_show_key.toggled.connect(self._toggle_api_key_visibility)
        self.ai_clear_key.clicked.connect(self._clear_api_key)
        self.ai_test_button.clicked.connect(self._test_ai_connection)
        return widget

    def reload(self) -> None:
        self._loading_settings = True
        self.settings = self.database.load_settings()
        self._reload_owners()
        self.auto_threshold.setValue(self.settings.auto_threshold)
        self.review_threshold.setValue(self.settings.review_threshold)
        concurrency_value = 0 if self.settings.concurrency_auto else self.settings.concurrency
        self.concurrency.setCurrentIndex(max(0, self.concurrency.findData(concurrency_value)))
        self.duplicate_policy.setCurrentText(self.settings.duplicate_policy)
        self.file_operation.setCurrentText(self.settings.file_operation)
        self.recognition_keywords.setPlainText("\n".join(self.settings.recognition_keywords))
        self.update_auto_check.setChecked(self.settings.update_auto_check)
        try:
            stored_key = load_api_key()
        except Exception:
            stored_key = ""
        self.ai_api_key.setText(stored_key)
        self._stored_api_key = stored_key
        self._ai_key_validated = bool(stored_key)
        self.ai_enabled.setChecked(self.settings.ai_enabled and bool(stored_key))
        self.ai_provider.setCurrentIndex(max(0, self.ai_provider.findData(self.settings.ai_provider)))
        self.ai_model.setCurrentIndex(max(0, self.ai_model.findData(self.settings.ai_model)))
        self.ai_status.setText("已启用" if self.ai_enabled.isChecked() else ("API Key 已保存" if stored_key else "尚未配置 API Key"))
        self._loading_settings = False

    def _reload_owners(self) -> None:
        rows = self.database.owner_rows()
        self.owner_table.setUpdatesEnabled(False)
        for row in range(self.owner_table.rowCount()):
            old_actions = self.owner_table.cellWidget(row, 3)
            if old_actions is not None:
                old_actions.hide()
                self.owner_table.removeCellWidget(row, 3)
                old_actions.deleteLater()
        self.owner_table.clearContents()
        self.owner_table.setRowCount(0)
        self.owner_table.setRowCount(len(rows))
        button_width = max(58, self.fontMetrics().horizontalAdvance("修改") + 30)
        self.owner_table.setColumnWidth(3, button_width * 2 + 22)
        for row, (owner_id, name, aliases) in enumerate(rows):
            self.owner_table.setItem(row, 0, QTableWidgetItem(str(owner_id)))
            self.owner_table.setItem(row, 1, QTableWidgetItem(name))
            self.owner_table.setItem(row, 2, QTableWidgetItem("、".join(aliases) or "-"))
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(5, 3, 5, 3)
            action_layout.setSpacing(6)
            modify = QPushButton("修改")
            delete = QPushButton("删除")
            modify.setFixedWidth(button_width)
            delete.setFixedWidth(button_width)
            modify.setMinimumHeight(30)
            delete.setMinimumHeight(30)
            delete.setObjectName("dangerButton")
            modify.clicked.connect(lambda checked=False, value=owner_id: self._edit_owner(value))
            delete.clicked.connect(lambda checked=False, value=owner_id: self._delete_owner(value))
            action_layout.addWidget(modify)
            action_layout.addWidget(delete)
            self.owner_table.setRowHeight(row, 42)
            self.owner_table.setCellWidget(row, 3, actions)
        self.owner_table.setUpdatesEnabled(True)
        self.owner_table.viewport().update()

    def _add_owner(self) -> None:
        names = _split_owner_names(self.new_owner.text())
        if not names:
            QMessageBox.warning(self, "无法添加", "请输入至少一个责任人姓名。")
            return

        added: list[str] = []
        errors: list[str] = []
        for name in names:
            try:
                self.database.add_owner(name)
                added.append(name)
            except Exception as exc:
                errors.append(f"{name}：{exc}")

        if added:
            self.new_owner.clear()
            self._reload_owners()
            self.owners_changed.emit()
        if errors:
            QMessageBox.warning(self, "部分姓名未添加", "\n".join(errors))

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
        concurrency_value = int(self.concurrency.currentData() or 0)
        return AppSettings(
            auto_threshold=self.auto_threshold.value(),
            review_threshold=self.review_threshold.value(),
            concurrency=(
                self.recommended_concurrency if concurrency_value == 0 else concurrency_value
            ),
            concurrency_auto=concurrency_value == 0,
            duplicate_policy=self.duplicate_policy.currentText(),
            file_operation=self.file_operation.currentText(),
            recognition_keywords=keywords,
            update_auto_check=self.update_auto_check.isChecked(),
            ai_enabled=self.ai_enabled.isChecked(),
            ai_provider=str(self.ai_provider.currentData() or "zhipu"),
            ai_model=str(self.ai_model.currentData() or "glm-4.6v-flash"),
            ai_timeout_seconds=self.settings.ai_timeout_seconds,
            ai_max_concurrency=self.settings.ai_max_concurrency,
        )

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.ai_api_key.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.ai_show_key.setText("隐藏" if visible else "显示")

    def _clear_api_key(self) -> None:
        clear_api_key()
        self.ai_api_key.clear()
        self._ai_key_validated = False
        self._stored_api_key = ""
        self._loading_settings = True
        self.ai_enabled.setChecked(False)
        self._loading_settings = False
        self.ai_status.setText("API Key 已清除")
        self._save_settings()

    def _test_ai_connection(self) -> None:
        if self.ai_connection_worker and self.ai_connection_worker.isRunning():
            return
        api_key = self.ai_api_key.text().strip()
        if not api_key:
            QMessageBox.warning(self, "无法测试", "请先填写 API Key。")
            return
        self._pending_api_key = api_key
        self.ai_test_button.setEnabled(False)
        self.ai_status.setText("正在使用内置测试文本连接智谱 AI…")
        self.ai_connection_worker = AiConnectionWorker(
            api_key, str(self.ai_model.currentData()), self.settings.ai_timeout_seconds, self,
        )
        self.ai_connection_worker.completed.connect(self._ai_connection_completed)
        self.ai_connection_worker.start()

    def _ai_connection_completed(self, ok: bool, message: str) -> None:
        self.ai_test_button.setEnabled(True)
        if ok:
            save_api_key(self._pending_api_key)
            self._stored_api_key = self._pending_api_key
            self._ai_key_validated = True
            self.ai_status.setText("连接成功，API Key 已加密保存")
        else:
            self._ai_key_validated = False
            self.ai_status.setText(f"连接失败：{message}")

    def _api_key_edited(self, value: str) -> None:
        self._ai_key_validated = bool(value.strip() and value.strip() == self._stored_api_key)
        if not self._ai_key_validated:
            self.ai_status.setText("API Key 已修改，请重新测试连接")

    def _ai_enabled_changed(self, enabled: bool) -> None:
        if self._loading_settings:
            return
        if enabled and not self._ai_key_validated:
            self._loading_settings = True
            self.ai_enabled.setChecked(False)
            self._loading_settings = False
            QMessageBox.warning(self, "无法启用", "请先填写 API Key 并测试连接。")
            return
        if enabled and QMessageBox.question(
            self, "启用 AI 增强",
            "低置信度结果会发送 OCR 摘要做语义辅助；本地 OCR 无法识别责任人时，会上传压缩后的疑似水印区域或整图做兜底识别。是否确认启用？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            self._loading_settings = True
            self.ai_enabled.setChecked(False)
            self._loading_settings = False
            return
        self.ai_status.setText("已启用" if enabled else "已关闭，本地 OCR 仍可正常使用")
        self._save_settings()

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

    def running_update_workers(self) -> list[QThread]:
        return [
            worker
            for worker in (self.update_check_worker, self.update_download_worker, self.ai_connection_worker)
            if worker is not None and worker.isRunning()
        ]

    def request_update_shutdown(self) -> list[QThread]:
        workers = self.running_update_workers()
        for worker in workers:
            worker.cancel()
        return workers

    def shutdown(self) -> None:
        self._keyword_save_timer.stop()
        for worker in self.request_update_shutdown():
            worker.wait()


class SettingsDialog(QDialog):
    def __init__(self, database: Database, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setObjectName("settingsDialog")
        self.setModal(True)
        self.resize(880, 680)
        self.setMinimumSize(760, 580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.page = SettingsPage(database, self, show_header=False)
        layout.addWidget(self.page, 1)
