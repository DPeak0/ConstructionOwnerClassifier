from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .dashboard import MainWindow, native_path_text
from .preview import ImagePreview, PreviewCanvas
from .single_instance import SingleInstanceGuard


APP_NAME = "施工责任人图片分类器"
INSTANCE_MUTEX_NAME = "Local\\ConstructionOwnerClassifier-5E4A77E2-62B8-4F57-9509-93B9EA343B22"


def _smoke_ocr() -> int:
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


def main() -> int:
    if "--smoke-ocr" in sys.argv:
        return _smoke_ocr()

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
        QMessageBox.information(
            None, "程序已在运行", "施工责任人图片分类器已经在运行，请勿重复打开。"
        )
        return 0
    try:
        window = MainWindow()
        window.show()
        return app.exec()
    finally:
        instance_guard.release()


__all__ = ["ImagePreview", "MainWindow", "PreviewCanvas", "main", "native_path_text"]


if __name__ == "__main__":
    raise SystemExit(main())
