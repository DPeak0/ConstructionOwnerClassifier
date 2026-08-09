from __future__ import annotations

import base64
import io
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image


PROVIDERS = {"zhipu": {"name": "智谱 AI", "models": {"glm-4.6v-flash": "GLM-4.6V-Flash"}}}


@dataclass(slots=True)
class AiReview:
    watermark_status: str
    label_seen: bool
    observed_name: str
    matched_owner: str
    name_legible: bool
    latency_ms: int = 0
    rejected_candidates: list[str] = field(default_factory=list)


def _jpeg_data_url(image: np.ndarray, max_side: int = 1200, quality: int = 82) -> str:
    pil = Image.fromarray(image).convert("RGB")
    if max(pil.size) > max_side:
        pil.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", pil.size)
    clean.paste(pil)
    stream = io.BytesIO()
    clean.save(stream, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _validate_review(
    data: Any, owners: list[str], candidates: list[tuple[str, float]] | None = None,
) -> AiReview:
    if not isinstance(data, dict):
        raise ValueError("AI 返回值不是对象")
    required = {
        "watermark_status", "label_seen", "observed_name", "matched_owner",
        "name_legible", "rejected_candidates",
    }
    if not required.issubset(data):
        raise ValueError("AI 返回字段不完整")
    status = data["watermark_status"]
    if status not in {"present", "absent", "uncertain"}:
        raise ValueError("AI 水印状态无效")
    if not isinstance(data["label_seen"], bool) or not isinstance(data["name_legible"], bool):
        raise ValueError("AI 布尔字段无效")
    if not isinstance(data["rejected_candidates"], list):
        raise ValueError("AI 候选过滤字段无效")
    observed = str(data["observed_name"] or "").strip()
    matched = str(data["matched_owner"] or "").strip()
    if matched and matched not in owners:
        matched = ""
    allowed_candidates = {name for name, _score in (candidates or [])}
    rejected = list(dict.fromkeys(
        str(name).strip() for name in data["rejected_candidates"]
        if str(name).strip() in allowed_candidates
    ))
    return AiReview(
        status, data["label_seen"], observed, matched, data["name_legible"],
        rejected_candidates=rejected,
    )


class GlmVisionReviewer:
    provider = "zhipu"

    def __init__(
        self, api_key: str, model: str = "glm-4.6v-flash", timeout: int = 30,
        semaphore: threading.Semaphore | None = None,
    ) -> None:
        from zai import ZhipuAiClient

        self.model = model
        self.timeout = timeout
        self._client = ZhipuAiClient(api_key=api_key, timeout=timeout, max_retries=0)
        self._semaphore = semaphore or threading.Semaphore(2)

    @staticmethod
    def _tools() -> list[dict[str, Any]]:
        return [{
            "type": "function",
            "function": {
                "name": "report_watermark_review",
                "description": "根据本地 OCR 文字、位置和候选返回语义复核结果",
                "parameters": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "watermark_status": {"type": "string", "enum": ["present", "absent", "uncertain"]},
                        "label_seen": {"type": "boolean"},
                        "observed_name": {"type": "string"},
                        "matched_owner": {"type": ["string", "null"]},
                        "name_legible": {"type": "boolean"},
                        "rejected_candidates": {
                            "type": "array", "items": {"type": "string"},
                            "description": "本地候选中可明确判定为非姓名的原词列表",
                        },
                    },
                    "required": ["watermark_status", "label_seen", "observed_name", "matched_owner", "name_legible", "rejected_candidates"],
                },
            },
        }]

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        name = type(exc).__name__
        return status == 429 or (isinstance(status, int) and status >= 500) or name in {
            "APIReachLimitError", "APIInternalError", "APIServerFlowExceedError"
        }

    def review(
        self, image: np.ndarray | None, ocr_summary: str, owners: list[str],
        candidates: list[tuple[str, float]], crop: np.ndarray | None = None,
        visual_fallback: bool = False,
    ) -> AiReview:
        semantic_prompt = (
            "你是本地 OCR 的语义复核器，只分析下面提供的 OCR 文字、坐标关系和候选。"
            "不要重新做图片识别，不要猜测 OCR 中未出现的姓名。"
            "matched_owner 只能从本地候选与责任人名单的交集中选择；证据不足时必须返回 null。"
            "施工责任人标签附近的姓名可作为支持证据，自然场景文字和其他表单字段不能作为姓名。"
            "逐项判断本地候选是否符合常见单姓/复姓与中文姓名结构，把明确的施工术语、地点、动作、天气等非姓名词放入 rejected_candidates。"
            "watermark_status 仅表达 OCR 语义是否支持存在水印，不能单独改变本地无水印结论。"
            "只通过指定函数返回。责任人名单："
            + json.dumps(owners, ensure_ascii=False)
            + "\n本地候选：" + json.dumps(candidates, ensure_ascii=False)
            + "\nOCR 坐标摘要：" + ocr_summary[:12000]
        )
        visual_prompt = (
            "本地 OCR 未能形成责任人名单候选。请结合图片与 OCR 摘要识别施工责任人水印。"
            "优先查找施工责任人、施工负责人或责任人标签附近的姓名，不要把自然场景文字当作姓名。"
            "matched_owner 只能从责任人名单中选择；名单外姓名放入 observed_name，不能虚构。"
            "同时逐项过滤本地候选，将明确非姓名词放入 rejected_candidates。"
            "这是视觉兜底结果，只用于人工复核。只通过指定函数返回。责任人名单："
            + json.dumps(owners, ensure_ascii=False)
            + "\n本地候选：" + json.dumps(candidates, ensure_ascii=False)
            + "\nOCR 坐标摘要：" + ocr_summary[:12000]
        )
        content: list[dict[str, Any]] = [{
            "type": "text", "text": visual_prompt if visual_fallback else semantic_prompt,
        }]
        if visual_fallback:
            upload = crop if crop is not None and crop.size else image
            if upload is None or not upload.size:
                raise ValueError("视觉兜底缺少可用图片")
            content.append({
                "type": "image_url", "image_url": {"url": _jpeg_data_url(upload)},
            })
        started = time.perf_counter()
        with self._semaphore:
            for attempt in range(2):
                try:
                    response = self._client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": content}],
                        tools=self._tools(),
                        tool_choice={"type": "function", "function": {"name": "report_watermark_review"}},
                        thinking={"type": "disabled"},
                    )
                    calls = response.choices[0].message.tool_calls or []
                    if not calls:
                        raise ValueError("AI 未返回结构化结果")
                    result = _validate_review(
                        json.loads(calls[0].function.arguments), owners, candidates,
                    )
                    result.latency_ms = round((time.perf_counter() - started) * 1000)
                    return result
                except Exception as exc:
                    if attempt == 0 and self._retryable(exc):
                        time.sleep(1.0)
                        continue
                    raise
        raise RuntimeError("AI 复核失败")

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.review(None, "内置连接测试文本，无用户数据", [], [])
            return True, "连接成功"
        except Exception as exc:
            return False, str(exc)
