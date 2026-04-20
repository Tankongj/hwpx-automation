"""Anthropic (Claude) 백엔드.

Messages API + tool_use 기반 structured output.

가격 기준: claude-haiku-4-5 — $1.00 input / $5.00 output per 1M tokens (2025 Q4).
"""
from __future__ import annotations

from typing import Any, Optional

from ..utils.logger import get_logger
from .gemini_resolver import GenerateResult


_log = get_logger("parser.anthropic")


DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# USD / 1M tokens
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-5-20250929": (3.00, 15.00),
    "claude-opus-4-1-20250805":   (15.00, 75.00),
}


_TOOL_NAME = "submit_resolution"
_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_no": {"type": "integer"},
                    "level": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["line_no", "level"],
            },
        }
    },
    "required": ["items"],
}


def _price_for(model: str) -> tuple[float, float]:
    if model in PRICE_TABLE:
        return PRICE_TABLE[model]
    # 대략 Haiku 단가로 fallback (가장 저렴, 비용 과소 추정 위험 적음)
    return PRICE_TABLE["claude-haiku-4-5-20251001"]


class AnthropicClient:
    """Anthropic Messages API 래퍼. :class:`ResolverClient` 프로토콜 준수."""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL) -> None:
        if api_key is None:
            from ..settings.api_key_manager import get_key

            api_key = get_key(service="anthropic")
        if not api_key:
            raise RuntimeError(
                "Anthropic API Key 가 설정되지 않았습니다. "
                "설정 탭 → AI 백엔드 → Anthropic 에서 등록하세요."
            )

        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "anthropic 패키지가 설치되어 있지 않습니다. "
                "`pip install anthropic` 후 다시 시도하세요."
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def generate(self, prompt: str) -> GenerateResult:
        """Claude 에 prompt 보내고 tool_use 로 구조화 JSON 받기."""
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=16384,
            temperature=0.1,
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": (
                        "판정한 레벨 결과를 제출하세요. items 배열에 각 line_no, level, reason 을 담으세요."
                    ),
                    "input_schema": _TOOL_INPUT_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )

        # tool_use 블록에서 items 추출
        import json as _json

        items: list[dict] = []
        for block in resp.content:
            # block.type == "tool_use"
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == _TOOL_NAME:
                tool_input = getattr(block, "input", {}) or {}
                items = list(tool_input.get("items", []))
                break

        text = _json.dumps(items, ensure_ascii=False)

        usage = getattr(resp, "usage", None)
        in_tok = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0

        stop_reason = (getattr(resp, "stop_reason", "") or "").upper()
        finish = {
            "END_TURN": "STOP",
            "TOOL_USE": "STOP",
            "MAX_TOKENS": "MAX_TOKENS",
            "STOP_SEQUENCE": "STOP",
        }.get(stop_reason, stop_reason)

        price_in, price_out = _price_for(self.model)
        return GenerateResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            thinking_tokens=0,
            finish_reason=finish,
            model=self.model,
            price_input_usd_per_m=price_in,
            price_output_usd_per_m=price_out,
        )


__all__ = ["DEFAULT_MODEL", "PRICE_TABLE", "AnthropicClient"]
