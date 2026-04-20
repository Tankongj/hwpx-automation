"""OpenAI 백엔드 — `openai` SDK 래퍼.

Chat Completions API + Structured Outputs (``response_format`` 의 ``json_schema``).
Gemini 와 유사한 ResolverClient 역할을 하므로 ``resolve()`` 에서 바로 쓸 수 있다.

가격 기준: gpt-4o-mini (2025 Q4 공식) — $0.15 input / $0.60 output per 1M tokens.
변경 시 :data:`PRICE_TABLE` 만 업데이트.
"""
from __future__ import annotations

from typing import Any, Optional

from ..utils.logger import get_logger
from .gemini_resolver import GenerateResult


_log = get_logger("parser.openai")


DEFAULT_MODEL = "gpt-4o-mini"

# USD / 1M tokens — 모델별 가격표. OpenAI 가 요율 변경하면 여기만 수정.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "gpt-4o-mini":        (0.15, 0.60),
    "gpt-4o":             (2.50, 10.00),
    "gpt-4.1-mini":       (0.40, 1.60),
    "gpt-4.1":            (2.00, 8.00),
    "o4-mini":            (1.10, 4.40),
}


# 응답 스키마 — Gemini 와 동일 구조
_RESOLVE_SCHEMA: dict[str, Any] = {
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
                "required": ["line_no", "level", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _price_for(model: str) -> tuple[float, float]:
    """모델 → (input_price, output_price). 미등록 모델은 gpt-4o-mini 단가 추정."""
    if model in PRICE_TABLE:
        return PRICE_TABLE[model]
    # 가장 가까운 기본값
    return PRICE_TABLE["gpt-4o-mini"]


class OpenAIClient:
    """OpenAI Chat Completions 래퍼. :class:`ResolverClient` 프로토콜 준수."""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL) -> None:
        if api_key is None:
            from ..settings.api_key_manager import get_key

            api_key = get_key(service="openai")
        if not api_key:
            raise RuntimeError(
                "OpenAI API Key 가 설정되지 않았습니다. "
                "설정 탭 → AI 백엔드 → OpenAI 에서 등록하세요."
            )

        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "openai 패키지가 설치되어 있지 않습니다. `pip install openai` 후 다시 시도하세요."
            ) from exc

        self._client = OpenAI(api_key=api_key)
        self.model = model

    def generate(self, prompt: str) -> GenerateResult:
        """Chat Completions 호출. JSON schema 응답 강제."""
        # Structured Outputs — object 를 요구하고, 배열은 object.items 로 래핑
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ResolveResult",
                    "strict": True,
                    "schema": _RESOLVE_SCHEMA,
                },
            },
            temperature=0.1,
            max_completion_tokens=16384,
        )

        choice = resp.choices[0]
        wrapped = choice.message.content or "{}"
        # wrapped 는 `{"items":[...]}` 형태 → items 배열만 꺼내 Gemini 와 동일 형태로
        import json as _json

        try:
            items = _json.loads(wrapped).get("items", [])
            text = _json.dumps(items, ensure_ascii=False)
        except _json.JSONDecodeError:
            text = wrapped  # parser 가 최종 처리

        usage = resp.usage
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0

        finish = (choice.finish_reason or "").upper()
        if finish == "LENGTH":
            finish = "MAX_TOKENS"
        elif finish == "STOP":
            finish = "STOP"

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


__all__ = ["DEFAULT_MODEL", "PRICE_TABLE", "OpenAIClient"]
