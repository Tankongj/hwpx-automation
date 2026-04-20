"""Instructor 라이브러리 기반 Unified Resolver — v0.12.0.

`instructor` (https://python.useinstructor.com/) 는 Gemini / OpenAI / Anthropic /
Ollama 등을 **Pydantic BaseModel** 로 통일된 structured output API 를 제공.

기존 (v0.3~v0.11):
- Gemini: `response_schema` + `types.Schema` 네이티브
- OpenAI:  `response_format` + json_schema
- Anthropic: tool_use
- Ollama: JSON 프롬프트 + parse
→ 4 가지 다른 코드 경로, 테스트도 각각

Instructor 통일 (v0.12):
- 모든 백엔드가 ``response_model=HierarchyArray`` 하나로 수렴
- 자동 retry + validation feedback
- **기존 경로 유지** (롤백 간편) — opt-in 플래그

**opt-in 방식**: ``AppConfig.use_instructor_resolver=True`` 일 때만 활성.
기본은 False → 기존 경로 그대로.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..utils.logger import get_logger
from .gemini_resolver import GenerateResult, ResolverClient


_log = get_logger("parser.instructor")


# ---------------------------------------------------------------------------
# Pydantic 응답 스키마
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, Field  # type: ignore
    _PYDANTIC_AVAILABLE = True

    class HierarchyDecision(BaseModel):
        """애매 라인 1 개에 대한 판정."""

        line_no: int = Field(description="원본 원고 줄 번호")
        level: int = Field(ge=-1, le=10, description="계층 레벨 -1~10")
        reason: str = Field(default="", description="짧은 판정 근거 (20자 이내)", max_length=40)

    class HierarchyResponse(BaseModel):
        """Resolver 응답 — 여러 라인의 판정 배열."""

        items: list[HierarchyDecision] = Field(
            description="각 line_no 에 대한 판정"
        )

except ImportError:
    _PYDANTIC_AVAILABLE = False

    class HierarchyDecision:  # type: ignore
        pass
    class HierarchyResponse:  # type: ignore
        pass


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """instructor + pydantic 둘 다 설치되어 있어야 True."""
    if not _PYDANTIC_AVAILABLE:
        return False
    try:
        import instructor  # noqa: F401  (type: ignore)
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class InstructorConfig:
    """Unified resolver 설정."""

    provider: str = "gemini"       # "gemini" / "openai" / "anthropic" / "ollama"
    model: str = "gemini-2.5-flash"
    api_key: Optional[str] = None
    max_retries: int = 2           # instructor 가 validation 실패 시 재시도 횟수


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class InstructorResolverClient:
    """ResolverClient 프로토콜 준수. 내부적으로 instructor 사용.

    4 백엔드 모두 **같은 생성자 + 같은 generate()**.

    Example::

        client = InstructorResolverClient(InstructorConfig(
            provider="gemini", model="gemini-2.5-flash", api_key="..."
        ))
        result = client.generate("...")  # GenerateResult
    """

    def __init__(self, config: InstructorConfig) -> None:
        if not is_available():
            raise ImportError(
                "instructor 또는 pydantic 미설치. "
                "`pip install instructor pydantic` 후 재시도하세요."
            )
        self.config = config
        self.model = f"instructor:{config.provider}:{config.model}"
        self._client = self._build_client(config)

    # ---- builder ----

    @staticmethod
    def _build_client(config: InstructorConfig):
        """provider 에 맞는 instructor client 생성."""
        import instructor  # type: ignore

        if config.provider == "gemini":
            from google import genai  # type: ignore
            raw = genai.Client(api_key=config.api_key)
            # instructor 의 google-genai 지원 — from_provider 방식
            return instructor.from_genai(raw)
        elif config.provider == "openai":
            from openai import OpenAI  # type: ignore
            raw = OpenAI(api_key=config.api_key)
            return instructor.from_openai(raw)
        elif config.provider == "anthropic":
            from anthropic import Anthropic  # type: ignore
            raw = Anthropic(api_key=config.api_key)
            return instructor.from_anthropic(raw)
        elif config.provider == "ollama":
            # Ollama 는 OpenAI-호환 엔드포인트로 instructor 가 붙음
            from openai import OpenAI  # type: ignore
            raw = OpenAI(
                base_url="http://localhost:11434/v1",
                api_key="ollama",  # placeholder
            )
            return instructor.from_openai(raw, mode=instructor.Mode.JSON)
        else:
            raise ValueError(f"알 수 없는 provider: {config.provider}")

    # ---- API (ResolverClient 준수) ----

    def generate(self, prompt: str) -> GenerateResult:
        """프롬프트 → JSON 배열 문자열 (기존 응답 형식과 호환).

        instructor 가 :class:`HierarchyResponse` pydantic 인스턴스를 반환해도,
        기존 ``gemini_resolver.resolve`` 는 JSON 문자열을 기대하므로 재직렬화.
        """
        import json

        try:
            response = self._client.chat.completions.create(  # type: ignore[attr-defined]
                model=self.config.model,
                response_model=HierarchyResponse,
                messages=[{"role": "user", "content": prompt}],
                max_retries=self.config.max_retries,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error("instructor 호출 실패: %s", exc)
            raise

        # 기존 파싱 파이프라인이 `items` 키를 기대하므로 dict 로 변환
        data = {
            "items": [d.model_dump() if hasattr(d, "model_dump") else dict(d.__dict__)
                      for d in response.items],
        }
        text = json.dumps(data, ensure_ascii=False)

        # 토큰 수는 provider 별로 추출. instructor 의 응답에서 얻기 어려우면 0.
        # (v0.13+ 에 instructor 의 raw response 접근법 개선)
        return GenerateResult(
            text=text,
            input_tokens=0,
            output_tokens=0,
            thinking_tokens=0,
            finish_reason="STOP",
            model=self.model,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_instructor_client(
    provider: str, model: str, *, api_key: Optional[str] = None,
) -> ResolverClient:
    """Instructor 기반 resolver client 생성.

    반환 타입은 :class:`~src.parser.gemini_resolver.ResolverClient` 프로토콜 호환.
    """
    cfg = InstructorConfig(provider=provider, model=model, api_key=api_key)
    return InstructorResolverClient(cfg)


__all__ = [
    "HierarchyDecision",
    "HierarchyResponse",
    "InstructorConfig",
    "InstructorResolverClient",
    "create_instructor_client",
    "is_available",
]
