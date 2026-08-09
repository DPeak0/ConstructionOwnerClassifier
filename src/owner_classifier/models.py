from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class RecordStatus(StrEnum):
    PENDING = "等待识别"
    PROCESSING = "识别中"
    REVIEW = "待复核"
    CONFIRMED = "已确认"
    CLASSIFIED = "已分类"
    UNRECOGNIZED = "未识别"
    NO_WATERMARK = "无水印"
    FAILED = "失败"


@dataclass(slots=True)
class TextBlock:
    text: str
    confidence: float
    box: list[list[float]] = field(default_factory=list)

    @property
    def center(self) -> tuple[float, float]:
        if not self.box:
            return (0.0, 0.0)
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    @property
    def height(self) -> float:
        if not self.box:
            return 24.0
        ys = [point[1] for point in self.box]
        return max(max(ys) - min(ys), 1.0)


@dataclass(slots=True)
class OcrResult:
    blocks: list[TextBlock]
    rotation: int = 0
    engine: str = ""
    error: str = ""

    @property
    def raw_text(self) -> str:
        return "\n".join(block.text for block in self.blocks)


@dataclass(slots=True)
class OwnerMatch:
    owner: str = ""
    confidence: float = 0.0
    matched_text: str = ""
    candidates: list[tuple[str, float]] = field(default_factory=list)
    margin: float = 0.0
    label_score: float = 0.0
    watermark_score: float = 0.0
    spelling_ambiguous: bool = False


@dataclass(slots=True)
class ClassificationRecord:
    source_path: str
    owner: str = ""
    candidate_owner: str = ""
    confidence: float = 0.0
    local_confidence: float = 0.0
    ocr_text: str = ""
    ocr_blocks: str = ""
    ocr_engine: str = ""
    rotation: int = 0
    status: RecordStatus = RecordStatus.PENDING
    output_path: str = ""
    error: str = ""
    sha256: str = ""
    reviewed: bool = False
    processed_at: str = ""
    record_id: int | None = None
    task_id: int | None = None
    candidate_owners: list[tuple[str, float]] = field(default_factory=list)
    watermark_score: float = 0.0
    owner_margin: float = 0.0
    recognition_evidence: str = ""
    ai_used: bool = False
    ai_provider: str = ""
    ai_model: str = ""
    ai_decision: str = ""
    ai_latency_ms: int = 0
    ai_error: str = ""
    decision_source: str = "local"

    @property
    def file_name(self) -> str:
        return Path(self.source_path).name

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = str(self.status)
        return data


@dataclass(slots=True)
class AppSettings:
    auto_threshold: float = 0.88
    review_threshold: float = 0.60
    concurrency: int = 1
    concurrency_auto: bool = True
    duplicate_policy: str = "重命名"
    file_operation: str = "复制"
    recognition_keywords: list[str] = field(
        default_factory=lambda: ["施工责任人", "施工负责人", "责任人"]
    )
    update_auto_check: bool = True
    ai_enabled: bool = False
    ai_provider: str = "zhipu"
    ai_model: str = "glm-4.6v-flash"
    ai_timeout_seconds: int = 30
    ai_max_concurrency: int = 2


@dataclass(slots=True)
class TaskSummary:
    task_id: int
    input_dir: str
    output_dir: str
    status: str
    created_at: str
    updated_at: str
    total: int = 0
    classified: int = 0
    review: int = 0
    unrecognized: int = 0
    failed: int = 0
    no_watermark: int = 0
