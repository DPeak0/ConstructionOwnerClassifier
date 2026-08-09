from __future__ import annotations

import re
import unicodedata

from rapidfuzz.fuzz import ratio

from .models import OcrResult, OwnerMatch, TextBlock


DEFAULT_LABELS = ("施工责任人", "施工负责人", "责任人")
FIELD_LABELS = (
    "施工区域", "施工内容", "施工单位", "建设单位", "拍摄时间", "时间",
    "天气", "地点", "工程记录",
)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\s:：;；,，。·|_\-—]+", "", value)
    return value.strip()


def _label_relation(label: TextBlock | None, candidate: TextBlock) -> float:
    if label is None:
        return 0.0
    lx, ly = label.center
    cx, cy = candidate.center
    height = max(label.height, candidate.height, 1.0)
    same_row = abs(cy - ly) <= height * 1.2 and cx >= lx
    next_row = 0 <= cy - ly <= height * 2.5
    if same_row:
        return 1.0
    if next_row:
        return 0.85
    return 0.25


def _candidate_text(block: TextBlock, labels: tuple[str, ...]) -> tuple[str, bool]:
    normalized = normalize_text(block.text)
    for label in labels:
        normalized_label = normalize_text(label)
        if normalized_label in normalized:
            return normalized.split(normalized_label, 1)[1], True
    return normalized, False


class OwnerParser:
    def __init__(
        self,
        owners: list[str] | dict[str, list[str]],
        labels: list[str] | tuple[str, ...] | None = None,
    ):
        if isinstance(owners, dict):
            self.owner_aliases = {
                owner.strip(): [alias.strip() for alias in aliases if alias.strip()]
                for owner, aliases in owners.items() if owner.strip()
            }
        else:
            self.owner_aliases = {owner.strip(): [] for owner in owners if owner.strip()}
        self.owners = list(self.owner_aliases)
        self.labels = tuple(label.strip() for label in (labels or DEFAULT_LABELS) if label.strip())

    def parse(self, result: OcrResult) -> OwnerMatch:
        if not result.blocks or not self.owners:
            return OwnerMatch()

        label_blocks = [
            block for block in result.blocks
            if any(normalize_text(label) in normalize_text(block.text) for label in self.labels)
        ]
        scored: dict[str, tuple[float, str]] = {}

        for block in result.blocks:
            text, contains_label = _candidate_text(block, self.labels)
            if any(normalize_text(field) == text for field in FIELD_LABELS):
                continue
            nearest_label = None
            if label_blocks:
                nearest_label = min(
                    label_blocks,
                    key=lambda item: abs(item.center[1] - block.center[1]) + max(0.0, item.center[0] - block.center[0]),
                )
            relation = 1.0 if contains_label and text else _label_relation(nearest_label, block)

            for owner, aliases in self.owner_aliases.items():
                similarity = 0.0
                for spelling in (owner, *aliases):
                    normalized_owner = normalize_text(spelling)
                    spelling_similarity = ratio(normalized_owner, text) / 100.0 if text else 0.0
                    if normalized_owner and normalized_owner in text:
                        spelling_similarity = 1.0
                    similarity = max(similarity, spelling_similarity)
                if similarity < 0.45:
                    continue
                score = 0.55 * similarity + 0.30 * max(0.0, min(block.confidence, 1.0)) + 0.15 * relation
                previous = scored.get(owner)
                if previous is None or score > previous[0]:
                    scored[owner] = (score, block.text)

        if not scored:
            return OwnerMatch()
        ordered = sorted(scored.items(), key=lambda item: item[1][0], reverse=True)
        owner, (confidence, matched_text) = ordered[0]
        return OwnerMatch(
            owner=owner,
            confidence=min(confidence, 1.0),
            matched_text=matched_text,
            candidates=[(name, round(data[0], 4)) for name, data in ordered[:5]],
        )
