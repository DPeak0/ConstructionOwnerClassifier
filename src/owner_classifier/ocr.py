from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
import hashlib
from importlib.resources import files
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from .models import OcrResult, TextBlock


RAPIDOCR_MODEL_SHA256 = {
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
}


@lru_cache(maxsize=1)
def verify_bundled_models() -> None:
    model_root = files("rapidocr").joinpath("models")
    for name, expected in RAPIDOCR_MODEL_SHA256.items():
        model = model_root.joinpath(name)
        if not model.is_file():
            raise RuntimeError(f"缺少内置 OCR 模型：{name}")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        if digest != expected:
            raise RuntimeError(f"内置 OCR 模型校验失败：{name}")


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


def gamma_image(image: np.ndarray, gamma: float = 0.65) -> np.ndarray:
    table = np.array([((value / 255.0) ** gamma) * 255 for value in range(256)], dtype=np.uint8)
    return cv2.LUT(image, table)


def sharpened_gray_image(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.2)
    sharp = cv2.addWeighted(gray, 1.8, blurred, -0.8, 0)
    return cv2.cvtColor(sharp, cv2.COLOR_GRAY2RGB)


def upscale_image(image: np.ndarray, factor: float = 2.0) -> np.ndarray:
    return cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def image_quality(image: np.ndarray) -> tuple[bool, dict[str, float]]:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    contrast = float(gray.std())
    acceptable = min(width, height) >= 240 and sharpness >= 18.0 and contrast >= 12.0
    return acceptable, {
        "width": float(width), "height": float(height),
        "sharpness": round(sharpness, 2), "contrast": round(contrast, 2),
    }


class RapidOcrProvider(OcrProvider):
    name = "RapidOCR PP-OCRv6 Small/ONNX"

    def __init__(self, inference_threads: int | None = None) -> None:
        from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        verify_bundled_models()
        threads = max(1, int(inference_threads or min(4, os.cpu_count() or 1)))
        self._engine = RapidOCR(params={
            "Global.log_level": "warning",
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            "Det.lang_type": LangDet.CH, "Det.model_type": ModelType.SMALL,
            "Det.ocr_version": OCRVersion.PPOCRV6, "Det.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.CH, "Rec.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
        })

    def recognize(self, image: np.ndarray | str | Path) -> OcrResult:
        try:
            raw = self._engine(image)
            boxes = raw.boxes if raw.boxes is not None else []
            texts = raw.txts if raw.txts is not None else []
            scores = raw.scores if raw.scores is not None else []
            blocks = [
                TextBlock(
                    text=str(text),
                    confidence=float(scores[index]) if index < len(scores) else 0.0,
                    box=[[float(value) for value in point] for point in boxes[index]],
                )
                for index, text in enumerate(texts) if index < len(boxes)
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


def create_local_provider(
    prefer_paddle: bool = False, inference_threads: int | None = None,
) -> OcrProvider:
    if prefer_paddle:
        try:
            return PaddleOcrProvider()
        except (ImportError, ModuleNotFoundError):
            pass
    return RapidOcrProvider(inference_threads)
