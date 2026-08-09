from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from owner_classifier.engine import RecognitionEngine
from owner_classifier.models import AppSettings, OcrResult, RecordStatus, TextBlock
from owner_classifier.ocr import OcrProvider


def box(x: float, y: float, width: float = 120, height: float = 35):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


class FakeProvider(OcrProvider):
    name = "fake"

    def __init__(self, results: list[OcrResult]):
        self.results = results
        self.calls = 0

    def recognize(self, image):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


def sample_image(path: Path) -> None:
    y, x = np.indices((500, 800))
    data = np.stack(((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256), axis=2).astype(np.uint8)
    Image.fromarray(data).save(path)


def test_high_confidence_owner_finishes_fast(tmp_path: Path):
    path = tmp_path / "owner.jpg"
    sample_image(path)
    provider = FakeProvider([OcrResult([TextBlock("施工责任人：张三", 0.98, box(10, 10))])])
    record = RecognitionEngine(provider, ["张三"], AppSettings()).classify(path)
    assert record.status == RecordStatus.CONFIRMED
    assert record.owner == "张三"
    assert provider.calls == 1
    assert record.owner_margin >= 0.12


def test_exhaustive_clean_image_is_no_watermark(tmp_path: Path):
    path = tmp_path / "clean.jpg"
    sample_image(path)
    provider = FakeProvider([OcrResult([])])
    record = RecognitionEngine(provider, ["张三"], AppSettings()).classify(path)
    assert record.status == RecordStatus.NO_WATERMARK
    assert provider.calls == 2


def test_label_without_legible_name_is_not_no_watermark(tmp_path: Path):
    path = tmp_path / "blurred.jpg"
    sample_image(path)
    result = OcrResult([TextBlock("施工责任入", 0.48, box(10, 10))])
    record = RecognitionEngine(FakeProvider([result]), ["张三"], AppSettings()).classify(path)
    assert record.status == RecordStatus.UNRECOGNIZED


def test_watermark_layout_fields_are_not_no_watermark(tmp_path: Path):
    path = tmp_path / "layout.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工内容", 0.91, box(10, 10)),
        TextBlock("拍摄时间", 0.93, box(10, 50)),
    ])
    record = RecognitionEngine(FakeProvider([result]), ["张三"], AppSettings()).classify(path)
    assert record.status == RecordStatus.UNRECOGNIZED
    assert record.watermark_score >= 0.55


def test_candidate_margin_blocks_automatic_classification(tmp_path: Path):
    path = tmp_path / "conflict.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人", 0.98, box(10, 10)),
        TextBlock("张三", 0.96, box(180, 10)),
        TextBlock("李四", 0.95, box(180, 50)),
    ])
    record = RecognitionEngine(FakeProvider([result]), ["张三", "李四"], AppSettings()).classify(path)
    assert record.owner_margin < 0.12
    assert record.status == RecordStatus.REVIEW


def test_single_character_name_ambiguity_requires_review_and_keeps_both_candidates(
    tmp_path: Path,
):
    path = tmp_path / "ambiguous-name.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人", 0.99, box(10, 10)),
        TextBlock("曹华斌", 0.99, box(180, 10)),
    ])
    provider = FakeProvider([result])
    record = RecognitionEngine(provider, ["曹华兵"], AppSettings()).classify(path)

    assert record.status == RecordStatus.REVIEW
    assert record.owner == ""
    assert record.decision_source == "name_spelling_ambiguity"
    assert {name for name, _score in record.candidate_owners} >= {"曹华兵", "曹华斌"}
    assert provider.calls == 2


def test_longer_unknown_name_is_not_auto_classified_as_shorter_owner(tmp_path: Path):
    path = tmp_path / "longer-name.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人：张三丰", 0.99, box(10, 10)),
    ])
    provider = FakeProvider([result])
    record = RecognitionEngine(provider, ["张三"], AppSettings()).classify(path)

    assert record.status == RecordStatus.REVIEW
    assert record.owner == ""
    assert record.candidate_owner == ""
    assert record.candidate_owners[0][0] == "张三丰"
    assert provider.calls == 2


def test_scene_name_cannot_auto_classify_without_watermark_evidence(tmp_path: Path):
    path = tmp_path / "scene-name.jpg"
    sample_image(path)
    result = OcrResult([TextBlock("张三", 0.99, box(10, 10))])
    provider = FakeProvider([result])
    record = RecognitionEngine(
        provider, ["张三"], AppSettings(auto_threshold=0.80)
    ).classify(path)

    assert record.status == RecordStatus.REVIEW
    assert record.owner == ""
    assert record.watermark_score == 0.0
    assert provider.calls == 2


def test_exact_name_auto_classifies_when_both_spellings_are_configured(tmp_path: Path):
    path = tmp_path / "two-distinct-owners.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人", 0.99, box(10, 10)),
        TextBlock("曹华斌", 0.99, box(180, 10)),
    ])
    record = RecognitionEngine(
        FakeProvider([result]), ["曹华兵", "曹华斌"], AppSettings()
    ).classify(path)

    assert record.status == RecordStatus.CONFIRMED
    assert record.owner == "曹华斌"
    assert {name for name, _score in record.candidate_owners} >= {"曹华兵", "曹华斌"}


class FakeReview:
    provider = "zhipu"
    model = "glm-4.6v-flash"

    def review(self, *_args):
        from owner_classifier.ai import AiReview
        return AiReview("present", True, "张三", "张三", True, 12)


class FakeVisualReview(FakeReview):
    def __init__(self):
        self.visual_fallback = False

    def review(self, *_args):
        self.visual_fallback = bool(_args[-1])
        return super().review(*_args)


class FakeUnknownNameVisualReview(FakeReview):
    def __init__(self):
        self.visual_fallback = False

    def review(self, *_args):
        from owner_classifier.ai import AiReview
        self.visual_fallback = bool(_args[-1])
        return AiReview("present", True, "张三丰", "", True, 12)


class FakeAmbiguousNameReview(FakeReview):
    def review(self, *_args):
        from owner_classifier.ai import AiReview
        return AiReview(
            "present", True, "曹华斌", "曹华兵", True, 12, ["曹华斌"]
        )


def test_ai_agreement_confirms_local_review(tmp_path: Path):
    path = tmp_path / "ai.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人", 0.72, box(10, 10)), TextBlock("张三", 0.62, box(180, 10))
    ])
    settings = AppSettings(ai_enabled=True)
    record = RecognitionEngine(FakeProvider([result]), ["张三"], settings, FakeReview()).classify(path)
    assert record.status == RecordStatus.CONFIRMED
    assert record.ai_used
    assert record.decision_source == "local+ai_semantic"


def test_ai_cannot_resolve_or_remove_single_character_name_ambiguity(tmp_path: Path):
    path = tmp_path / "ai-name-ambiguity.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人", 0.99, box(10, 10)),
        TextBlock("曹华斌", 0.99, box(180, 10)),
    ])
    record = RecognitionEngine(
        FakeProvider([result]), ["曹华兵"], AppSettings(ai_enabled=True),
        FakeAmbiguousNameReview(),
    ).classify(path)

    assert record.ai_used
    assert record.status == RecordStatus.REVIEW
    assert record.owner == ""
    assert record.decision_source == "name_spelling_ambiguity"
    assert {name for name, _score in record.candidate_owners} >= {"曹华兵", "曹华斌"}


def test_ai_visual_fallback_enters_review_without_auto_classifying(tmp_path: Path):
    path = tmp_path / "ai-fallback.jpg"
    sample_image(path)
    result = OcrResult([TextBlock("施工责任人", 0.62, box(10, 10))])
    reviewer = FakeVisualReview()
    record = RecognitionEngine(
        FakeProvider([result]), ["张三"], AppSettings(ai_enabled=True), reviewer,
    ).classify(path)
    assert reviewer.visual_fallback
    assert record.status == RecordStatus.REVIEW
    assert record.owner == ""
    assert record.candidate_owner == "张三"
    assert record.decision_source == "ai_visual_review"


def test_weak_known_candidate_does_not_suppress_ai_visual_fallback(tmp_path: Path):
    path = tmp_path / "ai-weak-known-candidate.jpg"
    sample_image(path)
    result = OcrResult([
        TextBlock("施工责任人：张三丰", 0.99, box(10, 10)),
    ])
    reviewer = FakeUnknownNameVisualReview()
    record = RecognitionEngine(
        FakeProvider([result]), ["张丰"], AppSettings(ai_enabled=True), reviewer,
    ).classify(path)

    assert reviewer.visual_fallback
    assert record.status == RecordStatus.REVIEW
    assert record.owner == ""
    assert record.candidate_owners[0][0] == "张三丰"
    assert record.decision_source == "ai_visual_review"


def test_owner_list_can_refresh_without_reloading_ocr_provider(tmp_path: Path):
    path = tmp_path / "new-owner.jpg"
    sample_image(path)
    result = OcrResult([TextBlock("施工责任人：张三", 0.98, box(10, 10))])
    provider = FakeProvider([result])
    engine = RecognitionEngine(provider, [], AppSettings())

    before = engine.classify(path)
    engine.update_owners(["张三"])
    after = engine.classify(path)

    assert before.status == RecordStatus.REVIEW
    assert after.status == RecordStatus.CONFIRMED
    assert after.owner == "张三"
