"""제출서류 체크리스트 모듈 — v0.6.0 foundation scaffold.

RFP/공고문 PDF → Gemini 로 필수 제출서류 목록 추출 → 사용자가 지정한 폴더의 파일들과
매칭 → 체크리스트 UI 에 ✅/⚠️/❌ 표시.

v0.6.0 foundation (이 세션)
-------------------------
- 데이터 모델: :class:`RequiredDocument`, :class:`ChecklistResult`
- RFP 추출 stub (Gemini document-processing 예정)
- 파일명 날짜 패턴 매처 (결정론)
- OCR fallback stub

TODO (본편 v0.6.0)
----------------
1. Gemini `document-processing` 로 RFP PDF → 필수서류 목록 JSON 추출
2. 파일명 날짜 매처 + Tesseract OCR fallback
3. 체크리스트 탭 UI
4. 샘플 RFP PDF 필요 (사용자 제공)

설계 문서: :file:`docs/CHECKLIST_DESIGN.md`
"""

from .models import ChecklistResult, DocumentStatus, MatchedFile, RequiredDocument

__all__ = [
    "ChecklistResult",
    "DocumentStatus",
    "MatchedFile",
    "RequiredDocument",
]
