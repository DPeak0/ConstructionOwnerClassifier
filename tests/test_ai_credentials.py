from __future__ import annotations

import os

import pytest
import json
import threading
from types import SimpleNamespace
import numpy as np

from owner_classifier.ai import GlmVisionReviewer, _validate_review
from owner_classifier.credentials import clear_api_key, load_api_key, mask_api_key, save_api_key


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_key_lifecycle(tmp_path):
    path = tmp_path / "key.dat"
    save_api_key("first-secret-api-key", path)
    assert load_api_key(path) == "first-secret-api-key"
    assert b"first-secret-api-key" not in path.read_bytes()
    save_api_key("replacement-secret", path)
    assert load_api_key(path) == "replacement-secret"
    clear_api_key(path)
    assert load_api_key(path) == ""


def test_api_key_masking_and_ai_owner_validation():
    assert "secret" not in mask_api_key("1234-secret-5678")
    review = _validate_review({
        "watermark_status": "present", "label_seen": True,
        "observed_name": "名单外姓名", "matched_owner": "名单外姓名", "name_legible": True,
        "rejected_candidates": [],
    }, ["张三"])
    assert review.matched_owner == ""


def test_invalid_ai_structure_is_rejected():
    with pytest.raises(ValueError, match="字段"):
        _validate_review({"watermark_status": "present"}, ["张三"])


def _response(payload):
    call = SimpleNamespace(function=SimpleNamespace(arguments=json.dumps(payload, ensure_ascii=False)))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _reviewer(outcomes):
    reviewer = object.__new__(GlmVisionReviewer)
    reviewer.model = "glm-4.6v-flash"
    reviewer.timeout = 30
    completions = FakeCompletions(outcomes)
    reviewer._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    reviewer._semaphore = threading.Semaphore(1)
    return reviewer, completions


class ApiFailure(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize("status", [429, 500, 503])
def test_ai_retries_rate_limit_and_server_errors_once(monkeypatch, status):
    payload = {
        "watermark_status": "present", "label_seen": True, "observed_name": "张三",
        "matched_owner": "张三", "name_legible": True, "rejected_candidates": [],
    }
    reviewer, completions = _reviewer([ApiFailure(status), _response(payload)])
    monkeypatch.setattr("owner_classifier.ai.time.sleep", lambda _seconds: None)
    result = reviewer.review(np.zeros((32, 32, 3), dtype=np.uint8), "[]", ["张三"], [])
    assert result.matched_owner == "张三"
    assert completions.calls == 2


@pytest.mark.parametrize("status", [401, 408])
def test_ai_does_not_retry_authentication_or_timeout(status):
    reviewer, completions = _reviewer([ApiFailure(status)])
    with pytest.raises(ApiFailure):
        reviewer.review(np.zeros((32, 32, 3), dtype=np.uint8), "[]", ["张三"], [])
    assert completions.calls == 1


def test_ai_rejects_invalid_tool_result():
    reviewer, _completions = _reviewer([_response({"watermark_status": "absent"})])
    with pytest.raises(ValueError):
        reviewer.review(np.zeros((32, 32, 3), dtype=np.uint8), "[]", [], [])


def test_ai_semantic_review_does_not_upload_image():
    payload = {
        "watermark_status": "present", "label_seen": True, "observed_name": "张三",
        "matched_owner": "张三", "name_legible": True, "rejected_candidates": [],
    }
    reviewer, completions = _reviewer([_response(payload)])
    reviewer.review(np.zeros((32, 32, 3), dtype=np.uint8), "[]", ["张三"], [("张三", 0.8)])
    content = completions.last_kwargs["messages"][0]["content"]
    assert [item["type"] for item in content] == ["text"]
    assert "不要重新做图片识别" in content[0]["text"]


def test_ai_visual_fallback_uploads_only_one_compressed_image():
    payload = {
        "watermark_status": "present", "label_seen": True, "observed_name": "张三",
        "matched_owner": "张三", "name_legible": True, "rejected_candidates": [],
    }
    reviewer, completions = _reviewer([_response(payload)])
    reviewer.review(
        np.zeros((64, 96, 3), dtype=np.uint8), "[]", ["张三"], [], None, True,
    )
    content = completions.last_kwargs["messages"][0]["content"]
    assert [item["type"] for item in content] == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("ZHIPU_API_KEY"), reason="ZHIPU_API_KEY is not configured")
def test_real_zhipu_connection_when_key_is_available():
    ok, message = GlmVisionReviewer(os.environ["ZHIPU_API_KEY"]).test_connection()
    assert ok, message
