"""Ollama 로컬 백엔드 — 완전 오프라인 Resolver.

Ollama (https://ollama.com) 는 사용자의 PC 에서 로컬 LLM 을 REST API 로 노출하는 런타임.
HWPX Automation 의 애매 블록 해석을 **원고 한 자도 외부 전송 없이** 수행할 수 있어,
공공기관/법무 등 프라이버시 민감 환경의 핵심 배포 경로.

공개 API
--------
- :class:`OllamaClient(host="http://localhost:11434", model="qwen2.5:7b")`
- :func:`probe_server(host)` — 서버 기동 여부 + 사용 가능 모델 목록

Ollama API 핵심 (0.5+)
----------------------
- ``POST /api/generate``  : 단일 completion. ``format`` 에 JSON schema 주면 구조화 출력.
- ``GET  /api/tags``      : 설치된 모델 목록.

응답 필드 (토큰 계산용)
---------------------
- ``prompt_eval_count``   : input tokens
- ``eval_count``          : output tokens
- ``done_reason``         : "stop" / "length" / "load" 등

비용은 0 원 (로컬). ResolveReport 에서 ``cost`` 는 자동으로 0 USD 로 표시.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from ..utils.logger import get_logger
from .gemini_resolver import GenerateResult


_log = get_logger("parser.ollama")


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"

# /api/generate 응답 타임아웃 — 로컬이지만 첫 호출 시 모델 로드로 수 초~수십 초 걸릴 수 있음
REQUEST_TIMEOUT = 600.0

# Ollama 의 structured output 용 JSON schema. Gemini 와 동일한 형태를 흉내낸다.
_RESOLVE_SCHEMA: dict[str, Any] = {
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


# ---------------------------------------------------------------------------
# Probe — 서버 상태 확인
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    ok: bool
    models: list[str]
    error: str = ""

    def summary(self) -> str:
        if not self.ok:
            return f"❌ {self.error or 'Ollama 서버에 연결할 수 없음'}"
        if not self.models:
            return (
                "⚠️ Ollama 서버는 응답했지만 설치된 모델이 없습니다. "
                "`ollama pull qwen2.5:7b` 로 먼저 모델을 받아주세요."
            )
        return f"✅ Ollama 정상. 사용 가능 모델 {len(self.models)} 개: {', '.join(self.models[:3])}…"


def probe_server(host: str = DEFAULT_HOST, timeout: float = 3.0) -> ProbeResult:
    """Ollama 서버가 살아있는지 + 설치된 모델 목록 반환. GUI 의 "서버 확인" 버튼 용."""
    try:
        resp = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except httpx.ConnectError:
        return ProbeResult(
            ok=False,
            models=[],
            error=f"{host} 에 연결 실패 — Ollama 가 실행 중인지 확인하세요",
        )
    except httpx.TimeoutException:
        return ProbeResult(ok=False, models=[], error=f"{host} 응답 시간 초과")
    except httpx.HTTPError as exc:
        return ProbeResult(ok=False, models=[], error=f"HTTP 오류: {exc}")
    except json.JSONDecodeError:
        return ProbeResult(ok=False, models=[], error="응답 JSON 파싱 실패")

    models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    return ProbeResult(ok=True, models=models)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OllamaClient:
    """Ollama REST API 클라이언트. :class:`ResolverClient` 프로토콜 준수.

    Parameters
    ----------
    host : Ollama 서버 URL (기본 ``http://localhost:11434``)
    model : 사용할 모델 태그 (기본 ``qwen2.5:7b``)
    timeout : HTTP 요청 타임아웃 (초)
    structured_output : False 로 두면 ``format`` schema 없이 프롬프트에만 의존 (구버전 Ollama 호환)
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = REQUEST_TIMEOUT,
        structured_output: bool = True,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.structured_output = structured_output

    # ---- public ----

    def generate(self, prompt: str) -> GenerateResult:
        """Ollama /api/generate 호출. :class:`GenerateResult` 반환."""
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                # Ollama 기본 num_predict 128 은 너무 작음 — 배치 응답에 부족.
                # Gemini 의 max_output_tokens 32K 와 맞춤.
                "num_predict": 32768,
            },
        }
        if self.structured_output:
            payload["format"] = _RESOLVE_SCHEMA

        try:
            resp = httpx.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Ollama 서버에 연결할 수 없습니다 ({self.host}). "
                "설정 탭에서 'Ollama 서버 확인' 을 눌러 상태를 점검하세요."
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Ollama 응답 시간 초과 ({self.timeout:.0f}s). "
                "큰 원고는 모델 로드 + 추론에 1~2분 걸릴 수 있습니다."
            ) from exc
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text[:300]
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"Ollama HTTP {exc.response.status_code}: {body}") from exc

        text = str(data.get("response", ""))
        input_tokens = int(data.get("prompt_eval_count") or 0)
        output_tokens = int(data.get("eval_count") or 0)
        done_reason = str(data.get("done_reason") or "").upper()
        # Ollama 의 "stop" → "STOP", "length" → "MAX_TOKENS" 에 매핑
        finish_reason = {
            "STOP": "STOP",
            "LENGTH": "MAX_TOKENS",
            "LOAD": "LOAD",
        }.get(done_reason, done_reason)

        return GenerateResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=0,
            finish_reason=finish_reason,
            model=self.model,
            # 로컬 실행 — 요금 없음
            price_input_usd_per_m=0.0,
            price_output_usd_per_m=0.0,
        )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "OllamaClient",
    "ProbeResult",
    "probe_server",
]
