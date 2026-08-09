from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import numpy as np

from .models import AppSettings, ClassificationRecord, OcrResult, RecordStatus
from .ocr import OcrProvider, enhanced_image, load_normalized_image
from .parser import OwnerParser


def _rotate(image: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90:
        return np.rot90(image, 1).copy()
    if angle == 180:
        return np.rot90(image, 2).copy()
    if angle == 270:
        return np.rot90(image, 3).copy()
    return image


class RecognitionEngine:
    def __init__(
        self,
        local_provider: OcrProvider,
        owners: list[str] | dict[str, list[str]],
        settings: AppSettings,
    ) -> None:
        self.local_provider = local_provider
        self.parser = OwnerParser(owners, settings.recognition_keywords)
        self.settings = settings

    def classify(self, path: str | Path) -> ClassificationRecord:
        source = str(Path(path).resolve())
        record = ClassificationRecord(source_path=source, status=RecordStatus.PROCESSING)
        try:
            image = load_normalized_image(source)
            best_result = OcrResult([], engine=self.local_provider.name)
            best_match = self.parser.parse(best_result)

            for angle in (0, 90, 270, 180):
                candidate_image = _rotate(image, angle)
                result = self.local_provider.recognize(candidate_image)
                result.rotation = angle
                match = self.parser.parse(result)
                if match.confidence > best_match.confidence or not best_result.blocks:
                    best_result, best_match = result, match
                if match.confidence >= self.settings.auto_threshold:
                    break

            if best_match.confidence < self.settings.review_threshold:
                contrast_result = self.local_provider.recognize(enhanced_image(image))
                contrast_match = self.parser.parse(contrast_result)
                if contrast_match.confidence > best_match.confidence:
                    best_result, best_match = contrast_result, contrast_match

            record.candidate_owner = best_match.owner
            record.local_confidence = best_match.confidence
            record.confidence = best_match.confidence
            record.ocr_text = best_result.raw_text
            record.ocr_blocks = json.dumps(
                [{"text": block.text, "confidence": block.confidence, "box": block.box} for block in best_result.blocks],
                ensure_ascii=False,
            )
            record.ocr_engine = best_result.engine
            record.rotation = best_result.rotation
            record.error = best_result.error

            if record.confidence >= self.settings.auto_threshold and record.candidate_owner:
                record.owner = record.candidate_owner
                record.status = RecordStatus.CONFIRMED
            elif record.confidence >= self.settings.review_threshold and record.candidate_owner:
                record.status = RecordStatus.REVIEW
            else:
                record.status = RecordStatus.UNRECOGNIZED
        except Exception as exc:
            record.status = RecordStatus.FAILED
            record.error = str(exc)
        record.processed_at = datetime.now().isoformat(timespec="seconds")
        return record
