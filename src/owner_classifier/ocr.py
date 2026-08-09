from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from .models import OcrResult, TextBlock


class OcrProvider(ABC):
    name = "OCR"

    @abstractmethod
    def recognize(self, image: np.ndarray | str | Path) -> OcrResult:
        raise NotImplementedError

    def test_connection(self) -> tuple[bool, str]:
        return False, "该 OCR 提供器不支持连接测试"


def load_normalized_image(path: str | Path) -> np.ndarray:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image = ImageEnhance.Contrast(image).enhance(1.08)
        return np.asarray(image)


def enhanced_image(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
    light, a, b = cv2.split(lab)
    light = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(light)
    return cv2.cvtColor(cv2.merge((light, a, b)), cv2.COLOR_LAB2RGB)


class RapidOcrProvider(OcrProvider):
    name = "RapidOCR/ONNX"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def recognize(self, image: np.ndarray | str | Path) -> OcrResult:
        try:
            raw, _ = self._engine(image)
            blocks = [
                TextBlock(
                    text=str(item[1]),
                    confidence=float(item[2]),
                    box=[[float(value) for value in point] for point in item[0]],
                )
                for item in (raw or [])
            ]
            return OcrResult(blocks=blocks, engine=self.name)
        except Exception as exc:
            return OcrResult(blocks=[], engine=self.name, error=str(exc))


class PaddleOcrProvider(OcrProvider):
    name = "PaddleOCR"

    def __init__(self) -> None:
        from paddleocr import PaddleOCR

        self._engine = PaddleOCR(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def recognize(self, image: np.ndarray | str | Path) -> OcrResult:
        try:
            blocks: list[TextBlock] = []
            for prediction in self._engine.predict(image):
                data: Any = getattr(prediction, "json", None)
                if callable(data):
                    data = data()
                if not isinstance(data, dict):
                    data = getattr(prediction, "res", {})
                data = data.get("res", data) if isinstance(data, dict) else {}
                texts = data.get("rec_texts", [])
                scores = data.get("rec_scores", data.get("rec_text_scores", []))
                polygons = data.get("dt_polys", data.get("rec_polys", []))
                for index, text in enumerate(texts):
                    score = float(scores[index]) if index < len(scores) else 0.0
                    polygon = polygons[index].tolist() if index < len(polygons) and hasattr(polygons[index], "tolist") else (polygons[index] if index < len(polygons) else [])
                    blocks.append(TextBlock(str(text), score, polygon))
            return OcrResult(blocks=blocks, engine=self.name)
        except Exception as exc:
            return OcrResult(blocks=[], engine=self.name, error=str(exc))


def create_local_provider(prefer_paddle: bool = True) -> OcrProvider:
    if prefer_paddle:
        try:
            return PaddleOcrProvider()
        except (ImportError, ModuleNotFoundError):
            pass
    return RapidOcrProvider()
