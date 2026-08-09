from owner_classifier.models import OcrResult, TextBlock
from owner_classifier.parser import OwnerParser, normalize_text


OWNERS = ["刘纪林", "吴万松", "吴绘其", "曹华斌", "郭成喜", "陈万智"]


def box(x: float, y: float, width: float = 120, height: float = 35):
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def test_normalizes_full_width_punctuation_and_spaces():
    assert normalize_text(" 施工责任人： 吴万松 ") == "施工责任人吴万松"


def test_extracts_inline_owner():
    result = OcrResult([TextBlock("施工责任人：吴万松", 0.91, box(10, 100))])
    match = OwnerParser(OWNERS).parse(result)
    assert match.owner == "吴万松"
    assert match.confidence >= 0.85


def test_extracts_owner_to_right_of_label():
    result = OcrResult([
        TextBlock("施工责任人", 0.82, box(10, 100)),
        TextBlock("刘纪林", 0.75, box(180, 100)),
    ])
    match = OwnerParser(OWNERS).parse(result)
    assert match.owner == "刘纪林"
    assert match.confidence >= 0.85


def test_fuzzy_matches_single_wrong_character_for_review():
    result = OcrResult([
        TextBlock("施工责任人", 0.90, box(10, 100)),
        TextBlock("曹华兵", 0.92, box(180, 100)),
    ])
    match = OwnerParser(OWNERS).parse(result)
    assert match.owner == "曹华斌"
    assert 0.60 <= match.confidence < 0.85


def test_ignores_unrelated_fields():
    result = OcrResult([
        TextBlock("施工内容", 0.98, box(10, 100)),
        TextBlock("施工单位", 0.96, box(10, 160)),
    ])
    assert OwnerParser(OWNERS).parse(result).owner == ""


def test_alias_matches_but_returns_canonical_owner():
    result = OcrResult([TextBlock("责任人：曹华斌", 0.96, box(10, 100))])
    match = OwnerParser({"曹华兵": ["曹华斌"]}).parse(result)
    assert match.owner == "曹华兵"
    assert match.confidence >= 0.85


def test_custom_recognition_keyword_is_used():
    result = OcrResult([
        TextBlock("现场负责人", 0.95, box(10, 100)),
        TextBlock("刘纪林", 0.95, box(180, 100)),
    ])
    match = OwnerParser(["刘纪林"], ["现场负责人"]).parse(result)
    assert match.owner == "刘纪林"
    assert match.confidence >= 0.85
