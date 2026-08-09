from owner_classifier.models import OcrResult, TextBlock
from owner_classifier.parser import OwnerParser, is_likely_person_name, normalize_text


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
    assert match.spelling_ambiguous
    assert {name for name, _score in match.candidates} >= {"曹华兵", "曹华斌"}


def test_longer_unknown_name_does_not_exact_match_shorter_configured_owner():
    result = OcrResult([
        TextBlock("施工责任人：张三丰", 0.99, box(10, 100)),
    ])
    match = OwnerParser(["张三"]).parse(result)
    assert match.owner == "张三"
    assert match.candidates[0][0] == "张三丰"
    assert match.candidates[0][1] > match.confidence


def test_scene_name_without_watermark_layout_has_no_watermark_evidence():
    match = OwnerParser(["张三"]).parse(OcrResult([
        TextBlock("张三", 0.99, box(10, 100)),
    ]))
    assert match.owner == "张三"
    assert match.watermark_score == 0.0


def test_exact_name_is_not_ambiguous_when_both_spellings_are_configured():
    result = OcrResult([
        TextBlock("施工责任人", 0.98, box(10, 100)),
        TextBlock("曹华兵", 0.98, box(180, 100)),
    ])
    match = OwnerParser(["曹华兵", "曹华斌"]).parse(result)
    assert match.owner == "曹华兵"
    assert not match.spelling_ambiguous
    assert match.margin >= 0.12
    assert {name for name, _score in match.candidates} >= {"曹华兵", "曹华斌"}


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
    assert not match.spelling_ambiguous


def test_custom_recognition_keyword_is_used():
    result = OcrResult([
        TextBlock("现场负责人", 0.95, box(10, 100)),
        TextBlock("刘纪林", 0.95, box(180, 100)),
    ])
    match = OwnerParser(["刘纪林"], ["现场负责人"]).parse(result)
    assert match.owner == "刘纪林"
    assert match.confidence >= 0.85


def test_extracts_raw_owner_candidate_when_name_is_not_in_owner_list():
    result = OcrResult([
        TextBlock("施工责任人", 0.82, box(10, 100)),
        TextBlock("刘纪林", 0.76, box(180, 100)),
        TextBlock("汕尾市南山头", 0.91, box(180, 170)),
    ])
    match = OwnerParser(["吴万松"]).parse(result)
    assert match.owner == ""
    assert match.matched_text == "刘纪林"
    assert match.candidates == [("刘纪林", 0.832)]


def test_extracts_inline_raw_owner_candidate_without_any_configured_owner():
    result = OcrResult([
        TextBlock("施工责任人：曹华斌", 0.80, box(10, 100)),
    ])
    match = OwnerParser([]).parse(result)
    assert match.owner == ""
    assert match.candidates == [("曹华斌", 0.86)]


def test_raw_owner_candidate_ignores_placeholder_text():
    result = OcrResult([
        TextBlock("施工责任人", 0.90, box(10, 100)),
        TextBlock("请输入内容", 0.95, box(180, 100)),
    ])
    assert OwnerParser([]).parse(result).candidates == []


def test_unknown_scene_text_does_not_reduce_known_owner_margin():
    result = OcrResult([
        TextBlock("施工责任人：吴万松", 0.99, box(10, 100)),
        TextBlock("打磨", 0.98, box(180, 170)),
    ])
    match = OwnerParser(["吴万松"]).parse(result)
    assert match.owner == "吴万松"
    assert match.margin == match.confidence


def test_person_name_structure_uses_common_surnames_and_filters_scene_words():
    assert is_likely_person_name("刘纪林")
    assert is_likely_person_name("欧阳娜娜")
    assert not is_likely_person_name("打磨")
    assert not is_likely_person_name("安全")
    assert not is_likely_person_name("安装")
    assert not is_likely_person_name("焊接")
    assert not is_likely_person_name("施工区域")


def test_raw_candidates_filter_non_name_words_near_owner_label():
    result = OcrResult([
        TextBlock("施工责任人", 0.95, box(10, 100)),
        TextBlock("打磨", 0.98, box(180, 100)),
        TextBlock("安全", 0.96, box(180, 145)),
    ])
    assert OwnerParser([]).parse(result).candidates == []
