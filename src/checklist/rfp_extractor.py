"""RFP (공고문 / 제안요청서) → 필수 제출서류 목록 추출.

**v0.6.0 본편**: Gemini document-processing 과 HWPX 텍스트 추출을 사용해 실제 구현.

지원 포맷
--------
- **PDF** : Gemini Files API 로 직접 업로드 → ``generate_content`` 에서 file 레퍼런스로 참조.
  텍스트 추출은 무료 (Gemini 3 기준), 구조화 출력은 output 토큰만 과금.
- **HWPX**: ``Contents/section*.xml`` 의 모든 `<hp:t>` 텍스트를 합쳐 plain text 로 추출한 뒤
  Gemini 에 전달. 텍스트 모드 사용 — 입력 토큰 과금됨 (PDF 보다 비쌈).

HWP (구형 바이너리) 는 직접 지원하지 않음. 사용자가 HWPX 로 먼저 변환해야 함.

구현 메모
--------
- 백엔드는 현재 Gemini 고정 (문서 처리 기능은 Gemini 에만 있음)
- 백엔드 클라이언트는 :mod:`src.parser.gemini_resolver` 의 `GoogleGenAIClient` 가 아닌,
  **문서 업로드가 필요해서** 직접 ``google.genai.Client`` 를 사용
- API Key 는 :mod:`src.settings.api_key_manager` 에서 로드
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Union

from lxml import etree

from ..utils.logger import get_logger
from .models import RequiredDocument


_log = get_logger("checklist.rfp_extractor")


PathLike = Union[str, Path]

NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
SUPPORTED_EXTENSIONS = {".pdf", ".hwpx", ".hwp"}
MAX_HWPX_TEXT_LEN = 100_000   # Gemini input 보호용 상한 (너무 긴 RFP 는 앞부분만)


# Gemini structured output schema — RequiredDocument 배열
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "영문 소문자 snake_case 식별자. 예: business_reg, financial_statement",
                    },
                    "name": {
                        "type": "string",
                        "description": "한글 서류명. 예: 사업자등록증 사본",
                    },
                    "is_required": {
                        "type": "boolean",
                        "description": "필수 제출(True) vs 선택(False)",
                    },
                    "max_age_days": {
                        "type": "integer",
                        "description": "발급일 제한(일). 예: 3개월이면 90. 제한 없으면 0",
                    },
                    "filename_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "파일명 매칭에 쓸 한국어/영어 키워드 2~4개",
                    },
                    "description": {
                        "type": "string",
                        "description": "RFP 원문에서 이 서류를 언급한 문장 1개 (짧게)",
                    },
                },
                "required": ["id", "name", "is_required", "filename_hints"],
            },
        },
    },
    "required": ["documents"],
}


_PROMPT = """이 공고문/제안요청서 문서에서 입찰 참가자가 제출해야 할 필수/선택 서류 목록을
빠짐없이 뽑아주세요.

각 서류별로:
- id: 영문 소문자 snake_case (예: business_reg, financial_statement, corp_seal)
- name: 한글 정식 명칭 (예: "사업자등록증 사본")
- is_required: true = 필수, false = 선택
- max_age_days: 발급일 제한(일). 예: "최근 3개월 이내" → 90. 제한 없음 → 0
- filename_hints: 파일명에서 찾을 한국어/영어 키워드 2~4개 (예: ["사업자등록증", "business_registration"])
- description: 원문에서 서류를 언급한 문장을 짧게 인용

참고:
- 재무제표, 인감증명서 같은 일반 서류도 꼭 포함
- 선택/첨부 서류도 is_required=false 로 포함
- "또는" 으로 대체 가능한 서류는 각각 별도 항목으로 (id 만 다르게)
- 정성제안서/정량제안서/제안발표자료 같은 본문 자체도 서류로 포함
"""


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_hwpx_text(hwpx_path: PathLike, max_len: int = MAX_HWPX_TEXT_LEN) -> str:
    """HWPX 의 모든 ``<hp:t>`` 텍스트를 합쳐 plain text 로 반환.

    여러 섹션(section0.xml, section1.xml, ...) 순서대로 처리. 너무 길면 앞부분만 잘라냄.

    **v0.12.0**: ``python-hwpx`` 라이브러리가 있으면 우선 사용 (깨끗한 출력, 표/리스트 까지
    자동 포매팅). 실패 / 미설치 시 기존 lxml 경로로 자동 fallback.
    """
    path = Path(hwpx_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    # 우선 경로: python-hwpx
    try:
        from ..hwpx import hwpx_lib_adapter  # lazy
    except ImportError:
        hwpx_lib_adapter = None  # type: ignore

    if hwpx_lib_adapter is not None and hwpx_lib_adapter.is_available():
        text = hwpx_lib_adapter.extract_text_safe(path, max_len=max_len)
        if text is not None:
            _log.debug(
                "extract_hwpx_text: python-hwpx v%s 경로 사용 (%d 자)",
                hwpx_lib_adapter.version(), len(text),
            )
            return text
        _log.info("python-hwpx 경로 실패 → lxml fallback")

    # Fallback: 기존 lxml 경로
    parts: list[str] = []
    with zipfile.ZipFile(path, "r") as z:
        section_names = sorted(
            n for n in z.namelist()
            if n.startswith("Contents/section") and n.endswith(".xml")
        )
        for name in section_names:
            root = etree.fromstring(z.read(name))
            sec_parts: list[str] = []
            # 최상위 <hp:p> 순회 — 단락 사이 줄바꿈
            for p in root.findall(f"{{{NS_HP}}}p"):
                line: list[str] = []
                for t in p.iter(f"{{{NS_HP}}}t"):
                    if t.text:
                        line.append(t.text)
                if line:
                    sec_parts.append("".join(line))
            parts.append("\n".join(sec_parts))
    text = "\n\n".join(parts)
    if len(text) > max_len:
        text = text[:max_len] + f"\n\n[... 이하 {len(text) - max_len:,} 문자 생략]"
    return text


# ---------------------------------------------------------------------------
# Parsing Gemini response
# ---------------------------------------------------------------------------

def _parse_response_to_docs(text: str) -> list[RequiredDocument]:
    """Gemini 응답 JSON → RequiredDocument 리스트."""
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _log.error("Gemini 응답 JSON 파싱 실패: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("documents")
    if not isinstance(items, list):
        return []

    results: list[RequiredDocument] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            doc = RequiredDocument(
                id=str(it["id"]).strip(),
                name=str(it["name"]).strip(),
                is_required=bool(it.get("is_required", True)),
                max_age_days=(int(it["max_age_days"]) if it.get("max_age_days") else None) or None,
                filename_hints=[str(k).strip() for k in it.get("filename_hints", []) if str(k).strip()],
                description=str(it.get("description", "")).strip(),
            )
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning("RequiredDocument 항목 스킵 (%s): %r", exc, it)
            continue
        if not doc.id or not doc.name:
            continue
        # max_age_days=0 은 "제한 없음" → None 으로 정규화
        if doc.max_age_days == 0:
            doc.max_age_days = None
        results.append(doc)
    return results


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def _extract_from_pdf_via_gemini(pdf_path: Path, api_key: str, model: str) -> list[RequiredDocument]:
    """PDF → Gemini Files API 업로드 → 구조화 응답 → RequiredDocument 리스트."""
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(api_key=api_key)

    _log.info("Gemini Files API 업로드: %s (%.1f MB)", pdf_path.name, pdf_path.stat().st_size / 1024 / 1024)
    uploaded = client.files.upload(file=str(pdf_path))

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_RESPONSE_SCHEMA,
        temperature=0.1,
        max_output_tokens=8192,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    resp = client.models.generate_content(
        model=model,
        contents=[uploaded, _PROMPT],
        config=config,
    )
    text = getattr(resp, "text", "") or ""
    usage = getattr(resp, "usage_metadata", None)
    if usage:
        _log.info(
            "RFP 추출: prompt=%s candidates=%s (PDF 텍스트 추출은 무료, 출력만 과금)",
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
        )
    return _parse_response_to_docs(text)


def _extract_from_text_via_gemini(
    text: str, api_key: str, model: str, *, label: str = "RFP"
) -> list[RequiredDocument]:
    """이미 추출된 텍스트 → Gemini → RequiredDocument 리스트."""
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    client = genai.Client(api_key=api_key)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_RESPONSE_SCHEMA,
        temperature=0.1,
        max_output_tokens=8192,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    full_prompt = f"[{label} 본문]\n{text}\n\n{_PROMPT}"
    resp = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config=config,
    )
    output_text = getattr(resp, "text", "") or ""
    usage = getattr(resp, "usage_metadata", None)
    if usage:
        _log.info(
            "RFP 추출(텍스트 모드): prompt=%s candidates=%s",
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
        )
    return _parse_response_to_docs(output_text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_rfp(
    rfp_path: PathLike,
    *,
    api_key: str | None = None,
    model: str = "gemini-2.5-flash",
) -> list[RequiredDocument]:
    """RFP 파일 → :class:`RequiredDocument` 목록.

    Parameters
    ----------
    rfp_path : ``.pdf`` 또는 ``.hwpx``. HWP 는 미지원 (HWPX 로 먼저 변환).
    api_key : 지정하지 않으면 :func:`api_key_manager.get_key` 로 Gemini 키 로드.
    model : Gemini 모델. 기본 gemini-2.5-flash.

    Raises
    ------
    FileNotFoundError, ValueError, RuntimeError
    """
    path = Path(rfp_path)
    if not path.exists():
        raise FileNotFoundError(str(path))

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"지원하지 않는 포맷: {ext}. "
            f"현재 PDF / HWPX 만 지원합니다 (HWP 는 HWPX 로 먼저 변환하세요)."
        )

    if api_key is None:
        from ..settings.api_key_manager import get_key
        api_key = get_key(service="gemini")
    if not api_key:
        raise RuntimeError(
            "Gemini API Key 가 등록되지 않아 RFP 추출을 할 수 없습니다. "
            "설정 탭에서 등록해 주세요."
        )

    if ext == ".pdf":
        return _extract_from_pdf_via_gemini(path, api_key, model)

    if ext == ".hwpx":
        text = extract_hwpx_text(path)
        if not text.strip():
            raise ValueError("HWPX 에서 텍스트를 추출하지 못했습니다 (빈 문서?)")
        return _extract_from_text_via_gemini(text, api_key, model, label=path.name)

    # HWP: v0.10.0 — pro 티어는 BodyText 전체 시도 (실패 시 자동 PrvText 폴백),
    # 무료 티어는 PrvText 미리보기만. 단, 테스트/CLI 에서 세션이 없으면 free 로 간주.
    from ..commerce import tier_gate
    prefer_full = tier_gate.is_allowed("pro")
    from .hwp_text import extract_hwp_text
    result = extract_hwp_text(path, prefer_full=prefer_full)
    if not result.text:
        raise ValueError(
            f"HWP 텍스트 추출 실패: {result.error}. "
            "LibreOffice 로 PDF 변환 후 다시 시도해 주세요."
        )
    label_suffix = (
        " (BodyText 전체 분석)" if result.is_full else " (PrvText 미리보기 — 앞부분만 분석)"
    )
    label = f"{path.name}{label_suffix}"
    return _extract_from_text_via_gemini(result.text, api_key, model, label=label)


def demo_required_documents() -> list[RequiredDocument]:
    """GUI 프로토타입 용 샘플 필수 서류 목록 (API Key 없을 때 데모)."""
    return [
        RequiredDocument(
            id="business_reg",
            name="사업자등록증 사본",
            is_required=True,
            max_age_days=365,
            filename_hints=["사업자등록증", "business_registration", "brn"],
            description="(데모) RFP 3.1: 최근 1년 이내 발급본",
        ),
        RequiredDocument(
            id="corp_seal",
            name="법인 인감증명서",
            is_required=True,
            max_age_days=90,
            filename_hints=["인감증명서", "법인인감", "corp_seal"],
            description="(데모) RFP 3.2: 3개월 이내 발급본",
        ),
        RequiredDocument(
            id="financial_statement",
            name="최근 3개년 재무제표",
            is_required=True,
            filename_hints=["재무제표", "결산재무제표", "financial"],
            description="(데모) RFP 3.3",
        ),
        RequiredDocument(
            id="proposal_qualitative",
            name="정성제안서",
            is_required=True,
            filename_hints=["정성제안서", "qualitative"],
            description="(데모) RFP 5.1",
        ),
        RequiredDocument(
            id="proposal_quantitative",
            name="정량제안서",
            is_required=True,
            filename_hints=["정량제안서", "quantitative"],
            description="(데모) RFP 5.2",
        ),
    ]


__all__ = [
    "extract_from_rfp",
    "extract_hwpx_text",
    "demo_required_documents",
    "SUPPORTED_EXTENSIONS",
]
