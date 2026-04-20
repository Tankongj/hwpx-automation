"""v0.8.0 — 정량제안서 셀 레이블 기반 타입 힌트.

완전한 typed mode (QuantField 대체) 는 샘플 더 확보 후 v0.9.x 본편에서. 현재는
**경량 힌트**: 레이블 셀의 텍스트를 보고 옆 셀의 예상 타입을 추정해 UI 에서 tooltip/placeholder
로 표시.

규칙 (매우 단순):
- "번호" / "No." → NUMBER
- "일자" / "년월일" / "YYYY" / "발급일" / "설립" → DATE
- "명" / "성명" / "대표자" → TEXT
- "전화" → PHONE_LIKE (TEXT 의 서브)
- "이메일" → EMAIL (TEXT)
- "주소" → ADDRESS (MULTILINE 권장)
- 단위 ("명", "원", "년", "건") → NUMBER
- 그 외 → TEXT

타입은 :class:`src.quant.models.FieldType` 재사용.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import FieldType


# 키워드 → (field_type, unit or '')
# 우선순위: 더 구체적인 패턴 먼저. "년도/연도" 는 숫자, "일자/발행일" 은 날짜.
_LABEL_RULES: list[tuple[re.Pattern[str], FieldType, str]] = [
    # NUMBER+년 (년도/연도/설립년도 — 날짜보다 먼저)
    (re.compile(r"(년도|연도|설립\s*년|설립\s*연도|year)"), FieldType.NUMBER, "년"),
    # 날짜 (발행/발급/등록/작성/기타 일자)
    (re.compile(r"(발행일|발급일|등록일|작성일|계약일|승인일|일자|년월일|년\s*월\s*일)"), FieldType.DATE, ""),
    (re.compile(r"(전화|연락처|휴대\s*폰|팩스|tel|fax)", re.IGNORECASE), FieldType.TEXT, ""),
    (re.compile(r"(이메일|email|e-mail)", re.IGNORECASE), FieldType.TEXT, ""),
    (re.compile(r"(주소|소재지|address)", re.IGNORECASE), FieldType.MULTILINE, ""),
    (re.compile(r"총\s*원|인원|직원|명\s*수|수\s*료|사원"), FieldType.NUMBER, "명"),
    (re.compile(r"(금액|단가|예산|가격|amount|price)"), FieldType.NUMBER, "원"),
    (re.compile(r"(번호|no\.?|ID|id)"), FieldType.NUMBER, ""),
    (re.compile(r"(건수|횟수|회\s*수)"), FieldType.NUMBER, "건"),
    (re.compile(r"(성명|이름|대표자|담당자|name)", re.IGNORECASE), FieldType.TEXT, ""),
    # 메모/비고/설명 같은 긴 서술
    (re.compile(r"(비고|특이사항|설명|메모|참고|remark|description)", re.IGNORECASE), FieldType.MULTILINE, ""),
]


def hint_for_label(label_text: str) -> tuple[FieldType, str]:
    """레이블 텍스트 → (FieldType, 단위 권장).

    매칭 실패 시 ``(FieldType.TEXT, "")``.
    """
    if not label_text:
        return FieldType.TEXT, ""
    normalized = re.sub(r"\s+", " ", label_text).strip()
    for pat, ftype, unit in _LABEL_RULES:
        if pat.search(normalized):
            return ftype, unit
    return FieldType.TEXT, ""


def summarize_hint(ftype: FieldType, unit: str = "") -> str:
    """UI tooltip 용 문자열. 예: 'NUMBER (명)' / 'DATE'."""
    if unit:
        return f"{ftype.name} ({unit})"
    return ftype.name


__all__ = ["hint_for_label", "summarize_hint"]
