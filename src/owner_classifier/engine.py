from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from .models import AppSettings, ClassificationRecord, OcrResult, OwnerMatch, RecordStatus
from .ocr import OcrProvider, enhanced_image, image_quality, load_normalized_image, upscale_image
from .parser import OwnerParser, label_similarity


Attempt = tuple[str, OcrResult, OwnerMatch, int]


def _rotate(image: np.ndarray, angle: int) -> np.ndarray:
    if angle == 90:
        return np.rot90(image, 1).copy()
    if angle == 270:
        return np.rot90(image, 3).copy()
    return image


def _target_crop(
    image: np.ndarray, result: OcrResult, labels: tuple[str, ...],
) -> np.ndarray | None:
    label_blocks = [
        block for block in result.blocks
        if block.box and max((label_similarity(block.text, label) for label in labels), default=0.0) >= 0.55
    ]
    if not label_blocks:
        return None
    label = max(
        label_blocks,
        key=lambda block: max((label_similarity(block.text, value) for value in labels), default=0.0),
    )
    points = np.asarray(label.box, dtype=float)
    min_x, min_y = points.min(axis=0)
    max_x, max_y = points.max(axis=0)
    height = max(max_y - min_y, 12.0)
    nearby = [
        block for block in result.blocks if block.box
        and abs(block.center[1] - label.center[1]) <= height * 3.5
        and block.center[0] >= label.center[0] - height * 2
    ]
    if nearby:
        nearby_points = np.asarray(
            [point for block in nearby for point in block.box], dtype=float
        )
        max_x = max(max_x, float(nearby_points[:, 0].max()))
        min_y = min(min_y, float(nearby_points[:, 1].min()))
        max_y = max(max_y, float(nearby_points[:, 1].max()))
    x1 = max(0, int(min_x - height * 2))
    y1 = max(0, int(min_y - height * 2.5))
    x2 = min(image.shape[1], int(max_x + height * 8))
    y2 = min(image.shape[0], int(max_y + height * 2.5))
    if x2 <= x1 or y2 <= y1:
        return None
    crop = image[y1:y2, x1:x2]
    if min(crop.shape[:2]) < 160:
        crop = upscale_image(crop, 1.5)
    return crop


class RecognitionEngine:
    def __init__(
        self,
        local_provider: OcrProvider,
        owners: list[str] | dict[str, list[str]],
        settings: AppSettings,
        ai_reviewer: Any | None = None,
    ) -> None:
        self.local_provider = local_provider
        self.parser = OwnerParser(owners, settings.recognition_keywords)
        self.settings = settings
        self.ai_reviewer = ai_reviewer

    def update_owners(self, owners: list[str] | dict[str, list[str]]) -> None:
        self.parser = OwnerParser(owners, self.settings.recognition_keywords)

    def _run(self, image: np.ndarray, angle: int, variant: str) -> Attempt:
        started = time.perf_counter()
        result = self.local_provider.recognize(image)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        result.rotation = angle
        return variant, result, self.parser.parse(result), elapsed_ms

    def _fuse(self, attempts: list[Attempt]) -> OwnerMatch:
        evidence: dict[str, list[float]] = {}
        matched_text: dict[str, str] = {}
        for _variant, _result, match, _elapsed_ms in attempts:
            for name, score in match.candidates:
                evidence.setdefault(name, []).append(float(score))
            if match.owner:
                matched_text[match.owner] = match.matched_text
        scored: list[tuple[str, float]] = []
        total_attempts = max(len(attempts), 1)
        for name, scores in evidence.items():
            repeat = min(len(scores) / total_attempts, 0.5)
            fused = min(
                1.0,
                0.82 * max(scores) + 0.14 * (sum(scores) / len(scores)) + 0.08 * repeat,
            )
            scored.append((name, round(fused, 4)))
        scored.sort(key=lambda item: item[1], reverse=True)
        if not scored:
            return OwnerMatch(
                label_score=max((item[2].label_score for item in attempts), default=0.0),
                watermark_score=max((item[2].watermark_score for item in attempts), default=0.0),
            )
        name, confidence = scored[0]
        known_owner = name if name in self.parser.owners else ""
        other_known_scores = [
            score for candidate, score in scored
            if candidate in self.parser.owners and candidate != known_owner
        ]
        second_known = max(other_known_scores, default=0.0)
        margin = confidence - second_known if known_owner else 0.0
        spelling_ambiguous = any(item[2].spelling_ambiguous for item in attempts)
        return OwnerMatch(
            owner=known_owner,
            confidence=confidence,
            matched_text=matched_text.get(name, name),
            candidates=scored if spelling_ambiguous else scored[:5],
            margin=max(0.0, margin),
            label_score=max((item[2].label_score for item in attempts), default=0.0),
            watermark_score=max((item[2].watermark_score for item in attempts), default=0.0),
            spelling_ambiguous=spelling_ambiguous,
        )

    def _automatic(self, match: OwnerMatch) -> bool:
        return bool(
            match.owner
            and not match.spelling_ambiguous
            and match.confidence >= self.settings.auto_threshold
            and match.margin >= 0.12
            and match.watermark_score >= 0.72
        )

    def _decisive_review(self, match: OwnerMatch, attempts: list[Attempt]) -> bool:
        if (
            match.confidence < self.settings.auto_threshold
            or match.watermark_score < 0.72
            or not match.candidates
        ):
            return False
        if match.spelling_ambiguous:
            top_name = match.candidates[0][0]
        else:
            top_name = match.candidates[0][0]
            if match.owner or top_name in self.parser.owners:
                return False
        supporting_attempts = sum(
            any(name == top_name for name, _score in attempt_match.candidates)
            for _variant, _result, attempt_match, _elapsed_ms in attempts
        )
        return supporting_attempts >= 2

    @staticmethod
    def _serialize_attempts(attempts: list[Attempt]) -> list[dict[str, Any]]:
        return [{
            "variant": variant,
            "rotation": result.rotation,
            "elapsed_ms": elapsed_ms,
            "error": result.error,
            "blocks": [
                {"text": block.text, "confidence": block.confidence, "box": block.box}
                for block in result.blocks
            ],
        } for variant, result, _match, elapsed_ms in attempts]

    def _apply_ai(
        self, record: ClassificationRecord, image: np.ndarray,
        crop: np.ndarray | None, visual_fallback: bool,
    ) -> None:
        if not self.ai_reviewer:
            return
        record.ai_used = True
        record.ai_provider = getattr(self.ai_reviewer, "provider", "zhipu")
        record.ai_model = getattr(self.ai_reviewer, "model", self.settings.ai_model)
        try:
            review = self.ai_reviewer.review(
                image, record.ocr_blocks, self.parser.owners, record.candidate_owners,
                crop, visual_fallback,
            )
            record.ai_latency_ms = review.latency_ms
            record.ai_decision = json.dumps({
                "watermark_status": review.watermark_status,
                "label_seen": review.label_seen,
                "observed_name": review.observed_name,
                "matched_owner": review.matched_owner,
                "name_legible": review.name_legible,
                "rejected_candidates": review.rejected_candidates,
            }, ensure_ascii=False)
            rejected = set(review.rejected_candidates)
            record.candidate_owners = [
                item for item in record.candidate_owners
                if (
                    record.decision_source == "name_spelling_ambiguity"
                    or item[0] in self.parser.owners
                    or item[0] not in rejected
                )
            ]
            if record.decision_source == "name_spelling_ambiguity":
                return
            local_owner = record.candidate_owner
            if review.matched_owner and local_owner:
                if (
                    review.matched_owner == local_owner
                    and record.local_confidence >= self.settings.review_threshold
                    and record.watermark_score >= 0.55
                ):
                    record.owner = local_owner
                    record.confidence = max(record.confidence, self.settings.auto_threshold)
                    record.status = RecordStatus.CONFIRMED
                    record.decision_source = "local+ai_semantic"
                elif review.matched_owner != local_owner:
                    record.status = RecordStatus.REVIEW
                    record.decision_source = "ai_semantic_conflict"
            elif local_owner:
                if local_owner in rejected:
                    record.status = RecordStatus.REVIEW
                    record.decision_source = "ai_semantic_conflict"
                else:
                    record.decision_source = "ai_semantic_uncertain"
            elif visual_fallback and review.matched_owner:
                record.candidate_owner = review.matched_owner
                record.candidate_owners.insert(0, (review.matched_owner, record.confidence))
                record.status = RecordStatus.REVIEW
                record.decision_source = "ai_visual_review"
            elif visual_fallback and review.observed_name and review.name_legible:
                if not any(name == review.observed_name for name, _ in record.candidate_owners):
                    record.candidate_owners.insert(0, (review.observed_name, record.confidence))
                record.status = RecordStatus.REVIEW
                record.decision_source = "ai_visual_review"
            elif visual_fallback and rejected and not record.candidate_owners:
                record.status = RecordStatus.UNRECOGNIZED
                record.decision_source = "ai_filtered_candidates"
        except Exception as exc:
            record.ai_error = str(exc)

    def classify(self, path: str | Path) -> ClassificationRecord:
        source = str(Path(path).resolve())
        record = ClassificationRecord(source_path=source, status=RecordStatus.PROCESSING)
        try:
            image = load_normalized_image(source)
            quality_ok, quality = image_quality(image)
            attempts: list[Attempt] = [self._run(image, 0, "original")]
            fused = self._fuse(attempts)
            crop = _target_crop(image, attempts[0][1], self.parser.labels)
            strategy = "fast"
            decisive_review = False

            if not self._automatic(fused):
                strategy = "two_pass"
                if crop is not None:
                    attempts.append(self._run(crop, 0, "watermark_crop"))
                else:
                    attempts.append(self._run(enhanced_image(image), 0, "clahe"))
                fused = self._fuse(attempts)
                decisive_review = self._decisive_review(fused, attempts)
                if decisive_review:
                    strategy = "two_pass_review"

            if not self._automatic(fused) and not decisive_review and crop is not None:
                strategy = "targeted"
                attempts.append(self._run(enhanced_image(crop), 0, "watermark_crop_clahe"))
                fused = self._fuse(attempts)
                decisive_review = self._decisive_review(fused, attempts)

            suspected_watermark = fused.watermark_score >= 0.55 or fused.label_score >= 0.55
            if not self._automatic(fused) and not decisive_review and suspected_watermark:
                strategy = "orientation_fallback"
                for angle in (90, 270):
                    attempts.append(self._run(_rotate(image, angle), angle, f"rotate_{angle}"))
                    fused = self._fuse(attempts)
                    if self._automatic(fused):
                        break

            serialized = self._serialize_attempts(attempts)
            best_attempt = max(attempts, key=lambda item: item[2].confidence)
            record.candidate_owner = fused.owner
            record.candidate_owners = list(fused.candidates)
            record.local_confidence = fused.confidence
            record.confidence = fused.confidence
            record.watermark_score = fused.watermark_score
            record.owner_margin = fused.margin
            record.ocr_text = best_attempt[1].raw_text
            record.ocr_blocks = json.dumps(serialized, ensure_ascii=False)
            record.ocr_engine = best_attempt[1].engine
            record.rotation = best_attempt[1].rotation
            errors = [item[1].error for item in attempts if item[1].error]
            record.recognition_evidence = json.dumps({
                "quality": quality,
                "quality_acceptable": quality_ok,
                "strategy": strategy,
                "attempt_count": len(attempts),
                "ocr_elapsed_ms": sum(item[3] for item in attempts),
                "label_score": round(fused.label_score, 4),
                "watermark_score": round(fused.watermark_score, 4),
                "owner_margin": round(fused.margin, 4),
                "spelling_ambiguous": fused.spelling_ambiguous,
            }, ensure_ascii=False)

            if errors and len(errors) == len(attempts):
                record.status = RecordStatus.FAILED
                record.error = errors[0]
            elif fused.spelling_ambiguous and fused.candidates:
                record.status = RecordStatus.REVIEW
                record.decision_source = "name_spelling_ambiguity"
            elif self._automatic(fused):
                record.owner = fused.owner
                record.status = RecordStatus.CONFIRMED
            elif fused.confidence >= self.settings.review_threshold and fused.candidates:
                record.status = RecordStatus.REVIEW
            elif (
                len(attempts) >= 2 and quality_ok and not errors
                and fused.label_score < 0.55 and fused.watermark_score < 0.55
            ):
                record.status = RecordStatus.NO_WATERMARK
            else:
                record.status = RecordStatus.UNRECOGNIZED

            has_known_candidate = any(
                name in self.parser.owners for name, _score in record.candidate_owners
            )
            visual_fallback = (
                record.decision_source != "name_spelling_ambiguity"
                and not record.candidate_owner
                and record.status in {RecordStatus.REVIEW, RecordStatus.UNRECOGNIZED}
            )
            if (
                self.settings.ai_enabled
                and record.status in {RecordStatus.REVIEW, RecordStatus.UNRECOGNIZED}
                and (has_known_candidate or visual_fallback)
            ):
                self._apply_ai(record, image, crop, visual_fallback)
        except Exception as exc:
            record.status = RecordStatus.FAILED
            record.error = str(exc)
        record.processed_at = datetime.now().isoformat(timespec="seconds")
        return record
