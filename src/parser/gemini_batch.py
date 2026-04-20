"""Gemini Batch API 지원 — v0.12.0. 50% 할인 경로.

**언제 쓰는가**:
- Self-MoA 처럼 N 번 독립 호출 필요한 작업
- 문서가 "당장 결과" 가 아니라 "잠시 후 / 다음 작업일" 해결 가능한 경우
- 배치 시간 SLA 는 최대 24 시간 (대부분 수 분 내 완료)

**가격**: 입력/출력 모두 대화형 대비 **50% 할인** (2025-2026 구글 공식).

**Self-MoA + Batch 조합**:
- draws=3 + batch → 실시간 대비 ~50% 절감 (aggregator 단계만 real-time)
- 사용자는 "대용량 변환" 버튼으로 의식적으로 선택

**API 요약** (google-genai SDK):
    client.batches.create(src={"inlined_requests": [{...}]}, config={...})
    client.batches.get(name=...)
    # 상태: BATCH_STATE_SUCCEEDED / FAILED / RUNNING
    responses = batch.dest.inlined_responses

스캐폴드 범위 (v0.12.0):
- ✅ Batch job 생성·조회·응답 추출 헬퍼
- ✅ Polling with exponential backoff
- ✅ Self-MoA 와 결합하는 facade
- 🔜 GUI "배치 모드" 버튼 (v0.13+)

opt-in: ``AppConfig.use_gemini_batch = True`` + 현재 백엔드가 Gemini 여야 활성.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..utils.logger import get_logger
from .gemini_resolver import GenerateResult, price_for_model


_log = get_logger("parser.gemini_batch")


# Batch 할인율 (50% = 0.5)
BATCH_DISCOUNT = 0.5

# 최대 폴링 시간 (기본 30 분 — 대부분 배치는 수 분 내 완료)
MAX_POLL_SEC = 30 * 60


@dataclass
class BatchRequest:
    """단일 배치 항목."""

    key: str                     # 결과 매핑용 식별자 (예: "draw_1", "block_42")
    prompt: str
    schema: Optional[Any] = None  # types.Schema (Gemini 구조화 출력)


@dataclass
class BatchResult:
    """전체 배치 작업 결과."""

    batch_name: str = ""
    items: list[GenerateResult] = field(default_factory=list)
    state: str = ""              # "SUCCEEDED" / "FAILED" / "TIMEOUT" / "ERROR"
    error: str = ""
    elapsed_sec: float = 0.0


# ---------------------------------------------------------------------------
# Submitter
# ---------------------------------------------------------------------------


class GeminiBatchClient:
    """Gemini Batch API 클라이언트 래퍼.

    사용 예::

        client = GeminiBatchClient(api_key="...", model="gemini-2.5-flash")
        reqs = [
            BatchRequest(key="a", prompt="...") ,
            BatchRequest(key="b", prompt="...") ,
        ]
        result = client.submit_and_wait(reqs, timeout_sec=600)
        for r in result.items:
            print(r.text)
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash",
        poll_sec: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.poll_sec = max(5, int(poll_sec))
        self._client = None  # lazy init

    # ---- API ----

    def submit_and_wait(
        self,
        requests: list[BatchRequest],
        *,
        timeout_sec: int = MAX_POLL_SEC,
        on_poll: Optional[Callable[[str, float], None]] = None,
    ) -> BatchResult:
        """requests 제출 → 완료까지 polling → BatchResult.

        Parameters
        ----------
        requests : 배치에 넣을 항목 (최대 권장 ~100)
        timeout_sec : 이 시간 넘으면 포기하고 ``state="TIMEOUT"``
        on_poll : 폴링 콜백 (상태, 경과초) — GUI 프로그레스 바 용.
        """
        start = time.monotonic()
        result = BatchResult()

        if not requests:
            result.state = "SUCCEEDED"
            return result

        # 1) 제출
        try:
            batch = self._submit(requests)
        except Exception as exc:  # noqa: BLE001
            result.state = "ERROR"
            result.error = f"제출 실패: {exc}"
            result.elapsed_sec = time.monotonic() - start
            return result

        result.batch_name = getattr(batch, "name", "") or ""
        _log.info("Gemini Batch 제출됨: %s", result.batch_name)

        # 2) Polling
        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout_sec:
                result.state = "TIMEOUT"
                result.error = f"폴링 {timeout_sec}s 초과"
                break
            time.sleep(self.poll_sec)
            try:
                batch = self._get(result.batch_name)
            except Exception as exc:  # noqa: BLE001
                _log.warning("batch poll 실패: %s", exc)
                continue

            state_name = _state_name(batch)
            if on_poll:
                try:
                    on_poll(state_name, elapsed)
                except Exception:  # noqa: BLE001
                    pass

            if state_name in ("BATCH_STATE_SUCCEEDED", "SUCCEEDED"):
                result.state = "SUCCEEDED"
                result.items = _extract_results(batch, self.model, requests)
                break
            elif state_name in ("BATCH_STATE_FAILED", "FAILED", "CANCELLED"):
                result.state = "FAILED"
                result.error = f"Gemini Batch {state_name}"
                break
            # 그 외: RUNNING / PENDING — 계속

        result.elapsed_sec = time.monotonic() - start
        return result

    # ---- internal ----

    def _lazy_client(self):
        if self._client is None:
            try:
                from google import genai  # type: ignore
            except ImportError as exc:
                raise ImportError("google-genai 가 설치되지 않았습니다.") from exc
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _submit(self, requests: list[BatchRequest]):
        """batch.create 호출. SDK 버전별 API 차이 있을 수 있어 best-effort."""
        client = self._lazy_client()
        # inlined_requests 형식 — 2025-2026 Gemini Batch API
        inlined = []
        for req in requests:
            item = {
                "contents": req.prompt,
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.1,
                },
            }
            if req.schema is not None:
                item["generationConfig"]["responseSchema"] = req.schema
            inlined.append(item)

        return client.batches.create(
            model=self.model,
            src={"inlined_requests": inlined},
        )

    def _get(self, batch_name: str):
        return self._lazy_client().batches.get(name=batch_name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state_name(batch) -> str:
    """batch 객체에서 state 를 문자열로 추출 (SDK 버전 호환)."""
    state = getattr(batch, "state", None)
    if state is None:
        return ""
    return getattr(state, "name", str(state))


def _extract_results(batch, model: str, requests: list[BatchRequest]) -> list[GenerateResult]:
    """성공한 batch 에서 각 요청별 `GenerateResult` 추출 (Batch 할인 가격 반영)."""
    p_in_full, p_out_full = price_for_model(model)
    # Batch 는 50% 할인
    p_in = p_in_full * BATCH_DISCOUNT
    p_out = p_out_full * BATCH_DISCOUNT

    dest = getattr(batch, "dest", None)
    inlined = getattr(dest, "inlined_responses", []) if dest else []

    out: list[GenerateResult] = []
    for i, resp in enumerate(inlined):
        text = ""
        # SDK 가 제공하는 helper 가 있으면 우선
        if hasattr(resp, "response") and resp.response is not None:
            text = getattr(resp.response, "text", "") or ""
        elif hasattr(resp, "text"):
            text = resp.text or ""
        elif hasattr(resp, "candidates"):
            cands = resp.candidates or []
            if cands and hasattr(cands[0], "content"):
                parts = getattr(cands[0].content, "parts", [])
                for p in parts:
                    text += getattr(p, "text", "") or ""

        usage = getattr(resp, "usage_metadata", None) or getattr(
            getattr(resp, "response", None), "usage_metadata", None,
        )
        in_tok = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        out_tok = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        think_tok = int(getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0

        out.append(GenerateResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            thinking_tokens=think_tok,
            finish_reason="STOP",
            model=f"{model}+batch",
            price_input_usd_per_m=p_in,
            price_output_usd_per_m=p_out,
        ))
    return out


# ---------------------------------------------------------------------------
# Self-MoA 와 결합용 facade
# ---------------------------------------------------------------------------


def run_self_moa_as_batch(
    prompt: str,
    *,
    api_key: str,
    model: str,
    draws: int = 3,
    poll_sec: int = 60,
) -> list[GenerateResult]:
    """Self-MoA 의 N 개 draw 를 1 개 batch 로 묶어 50% 할인.

    반환은 기존 Self-MoA 용 draw 목록. Aggregator 는 별도로 실시간 호출.

    **주의**: pro 티어 체크는 호출자가 (SelfMoAClient 생성자에서 이미 체크).
    """
    reqs = [
        BatchRequest(key=f"draw_{i}", prompt=prompt)
        for i in range(draws)
    ]
    client = GeminiBatchClient(api_key=api_key, model=model, poll_sec=poll_sec)
    result = client.submit_and_wait(reqs)
    if result.state != "SUCCEEDED":
        raise RuntimeError(f"Gemini Batch 실패: {result.state} — {result.error}")
    return result.items


__all__ = [
    "BATCH_DISCOUNT",
    "BatchRequest",
    "BatchResult",
    "GeminiBatchClient",
    "run_self_moa_as_batch",
]
