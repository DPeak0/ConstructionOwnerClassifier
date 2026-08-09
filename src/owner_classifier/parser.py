from __future__ import annotations

import re
import unicodedata

from rapidfuzz.fuzz import partial_ratio, ratio

from .models import OcrResult, OwnerMatch, TextBlock


DEFAULT_LABELS = ("施工责任人", "施工负责人", "责任人")
FIELD_LABELS = (
    "施工区域", "施工内容", "施工单位", "建设单位", "拍摄时间", "时间",
    "天气", "地点", "工程记录",
)
INVALID_OWNER_VALUES = {
    "请输入内容", "点击编辑本条内容", "未填写", "未录入", "暂无", "无",
}
NON_NAME_TERMS = {
    "安全", "质量", "施工", "现场", "项目", "工程", "打磨", "厂房", "天地",
    "天气", "内容", "区域", "单位", "时间", "地点", "管理", "检查", "完成",
    "进度", "照片", "拍摄", "记录", "人员", "负责人", "责任人",
    "安装", "拆除", "焊接", "切割", "浇筑", "绑扎", "支模", "吊装", "清理",
    "整改", "放线", "测量", "开挖", "回填", "搬运", "防护", "验收", "交底",
    "作业", "工人", "班组", "机械", "材料", "钢筋", "模板", "混凝土", "基础",
    "楼层", "核岛", "厂区", "上午", "下午", "晴天", "阴天", "雨天", "高处",
    "临边", "洞口",
}
CHINESE_NAME_PATTERN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]{2,6}$")
COMMON_SINGLE_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费"
    "廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董"
    "梁杜阮蓝闵席季麻强贾路娄江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡"
    "凌霍虞万柯管卢莫房陆荣翁荀羊惠甄魏家封储靳段巫焦巴牧山谷车侯"
    "全班秋仲伊宫宁栾甘厉祖武符刘景詹龙叶司黎白蒲鄂索赖卓蔺屠池乔"
    "谭申冉桑桂牛边燕尚温庄晏柴瞿阎连茹艾向古易廖耿匡文寇广欧沃利"
    "越师聂晁辛简饶曾沙鞠关查荆游权盖益桓公"
)
COMMON_COMPOUND_SURNAMES = {
    "欧阳", "司马", "上官", "诸葛", "东方", "独孤", "南宫", "万俟",
    "闻人", "夏侯", "皇甫", "尉迟", "公羊", "澹台", "公冶", "宗政",
    "濮阳", "淳于", "单于", "太叔", "申屠", "公孙", "仲孙", "轩辕",
    "令狐", "钟离", "宇文", "长孙", "慕容", "鲜于", "司徒", "司空",
    "端木", "百里", "东郭", "南门", "呼延", "羊舌", "微生", "左丘",
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\s:：;；,，。·|_\-—]+", "", value)
    return value.strip()


def label_similarity(text: str, label: str) -> float:
    normalized_text = normalize_text(text)
    normalized_label = normalize_text(label)
    if not normalized_text or not normalized_label:
        return 0.0
    if normalized_label in normalized_text:
        return 1.0
    return partial_ratio(normalized_label, normalized_text) / 100.0


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


def _raw_owner_name(value: str) -> str:
    name = normalize_text(value)
    if not is_likely_person_name(name):
        return ""
    if any(normalize_text(field) == name for field in FIELD_LABELS):
        return ""
    return name


def is_likely_person_name(value: str) -> bool:
    name = normalize_text(value)
    if (
        name in INVALID_OWNER_VALUES
        or any(term in name for term in NON_NAME_TERMS)
        or not CHINESE_NAME_PATTERN.fullmatch(name)
        or not 2 <= len(name) <= 4
    ):
        return False
    if name[:2] in COMMON_COMPOUND_SURNAMES:
        return 3 <= len(name) <= 4
    return name[:1] in COMMON_SINGLE_SURNAMES


def _name_likelihood(name: str) -> float:
    surname_score = 1.0 if (
        name[:2] in COMMON_COMPOUND_SURNAMES or name[:1] in COMMON_SINGLE_SURNAMES
    ) else 0.4
    length_score = 1.0 if 2 <= len(name) <= 4 else 0.6
    return 0.75 * surname_score + 0.25 * length_score


def _single_character_substitution(left: str, right: str) -> bool:
    left = normalize_text(left)
    right = normalize_text(right)
    return (
        len(left) == len(right)
        and 2 <= len(left) <= 4
        and sum(a != b for a, b in zip(left, right)) == 1
    )


def _raw_owner_candidates(
    blocks: list[TextBlock], labels: tuple[str, ...], label_blocks: list[TextBlock]
) -> list[tuple[str, float]]:
    scored: dict[str, float] = {}

    def add_candidate(block: TextBlock, name: str, relation: float) -> None:
        score = (
            0.70 * max(0.0, min(block.confidence, 1.0))
            + 0.20 * relation
            + 0.10 * _name_likelihood(name)
        )
        scored[name] = max(scored.get(name, 0.0), min(score, 1.0))

    for block in blocks:
        text, contains_label = _candidate_text(block, labels)
        if contains_label and text:
            name = _raw_owner_name(text)
            if name:
                add_candidate(block, name, 1.0)

    for label in label_blocks:
        lx, ly = label.center
        same_row: list[tuple[float, TextBlock, str]] = []
        next_row: list[tuple[float, TextBlock, str]] = []
        for block in blocks:
            if block is label:
                continue
            name = _raw_owner_name(_candidate_text(block, labels)[0])
            if not name:
                continue
            cx, cy = block.center
            height = max(label.height, block.height, 1.0)
            horizontal_distance = max(0.0, cx - lx) / height
            if abs(cy - ly) <= height * 1.2 and cx >= lx:
                same_row.append((abs(cy - ly) / height + horizontal_distance * 0.03, block, name))
            elif 0 < cy - ly <= height * 2.5:
                next_row.append(((cy - ly) / height + abs(cx - lx) / height * 0.03, block, name))
        pool = same_row or next_row
        if pool:
            _distance, block, name = min(
                pool,
                key=lambda item: (
                    item[0] - 0.2 * _name_likelihood(item[2]),
                    -item[1].confidence,
                ),
            )
            add_candidate(block, name, 1.0 if same_row else 0.85)
    return sorted(scored.items(), key=lambda item: item[1], reverse=True)


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
        if not result.blocks:
            return OwnerMatch()

        block_label_scores = {
            id(block): max((label_similarity(block.text, label) for label in self.labels), default=0.0)
            for block in result.blocks
        }
        label_blocks = [block for block in result.blocks if block_label_scores[id(block)] >= 0.72]
        label_score = max(block_label_scores.values(), default=0.0)
        layout_hits = sum(
            1 for block in result.blocks
            if max((label_similarity(block.text, field) for field in FIELD_LABELS), default=0.0) >= 0.82
        )
        layout_score = 0.62 if layout_hits >= 2 else (0.55 if layout_hits == 1 else 0.0)
        raw_candidates = _raw_owner_candidates(result.blocks, self.labels, label_blocks)
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
                    if normalized_owner and normalized_owner == text:
                        spelling_similarity = 1.0
                    similarity = max(similarity, spelling_similarity)
                if similarity < 0.45:
                    continue
                score = 0.50 * similarity + 0.35 * max(0.0, min(block.confidence, 1.0)) + 0.15 * relation
                previous = scored.get(owner)
                if previous is None or score > previous[0]:
                    scored[owner] = (score, block.text)

        ordered = sorted(scored.items(), key=lambda item: item[1][0], reverse=True)
        merged_candidates: dict[str, float] = {
            name: round(data[0], 4) for name, data in ordered
        }
        recognized_spellings = {
            normalize_text(spelling): owner
            for owner, aliases in self.owner_aliases.items()
            for spelling in (owner, *aliases)
        }
        ambiguous_names: set[str] = set()
        for name, _score in raw_candidates:
            normalized_name = normalize_text(name)
            if normalized_name in recognized_spellings:
                continue
            for owner, aliases in self.owner_aliases.items():
                if any(
                    _single_character_substitution(normalized_name, spelling)
                    for spelling in (owner, *aliases)
                ):
                    ambiguous_names.update((name, owner))
        for name, score in raw_candidates:
            canonical = recognized_spellings.get(normalize_text(name))
            if canonical and canonical in merged_candidates:
                continue
            merged_candidates[name] = max(merged_candidates.get(name, 0.0), round(score, 4))
        all_candidates = sorted(
            merged_candidates.items(), key=lambda item: item[1], reverse=True
        )
        candidates = all_candidates[:5]
        included = {name for name, _score in candidates}
        candidates.extend(
            (name, score) for name, score in all_candidates
            if name in ambiguous_names and name not in included
        )
        spelling_ambiguous = bool(ambiguous_names)
        if not ordered:
            if not candidates:
                return OwnerMatch(label_score=label_score, watermark_score=max(label_score, layout_score))
            name, confidence = candidates[0]
            return OwnerMatch(
                confidence=confidence,
                matched_text=name,
                candidates=candidates,
                margin=confidence - (candidates[1][1] if len(candidates) > 1 else 0.0),
                label_score=label_score,
                watermark_score=max(label_score, layout_score),
                spelling_ambiguous=spelling_ambiguous,
            )
        owner, (confidence, matched_text) = ordered[0]
        second_known_score = ordered[1][1][0] if len(ordered) > 1 else 0.0
        return OwnerMatch(
            owner=owner,
            confidence=min(confidence, 1.0),
            matched_text=matched_text,
            candidates=candidates,
            margin=max(0.0, confidence - second_known_score),
            label_score=label_score,
            watermark_score=max(label_score, layout_score),
            spelling_ambiguous=spelling_ambiguous,
        )
