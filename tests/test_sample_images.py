from pathlib import Path

import pytest

from owner_classifier.engine import RecognitionEngine
from owner_classifier.models import AppSettings
from owner_classifier.ocr import create_local_provider


@pytest.mark.integration
def test_supplied_samples_resolve_to_parent_owner():
    root = Path(__file__).resolve().parents[1]
    source_owners = ["刘纪林", "吴万松", "吴绘其", "曹华斌", "郭成喜", "陈万智"]
    owners = {"刘纪林": [], "吴万松": [], "吴绘其": [], "曹华兵": ["曹华斌"], "郭成喜": [], "陈万智": []}
    samples = [path for owner in source_owners for path in (root / owner).glob("*.jpg")]
    if not samples:
        pytest.skip("外部参考样本未放入本地工作区")
    assert len(samples) == 10

    engine = RecognitionEngine(create_local_provider(prefer_paddle=False), owners, AppSettings())
    mismatches = []
    for path in samples:
        record = engine.classify(path)
        expected = "曹华兵" if path.parent.name == "曹华斌" else path.parent.name
        if record.candidate_owner != expected:
            mismatches.append((path.name, expected, record.candidate_owner, record.confidence))
    assert not mismatches
