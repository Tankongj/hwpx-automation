# 제출서류 체크리스트 설계 (v0.6.0 예정)

**상태**: foundation scaffold 완료 (`src/checklist/`). 본편 구현은 **사용자 RFP 샘플 입수 후**.

---

## 1. 문제 정의

공고문(RFP) 에 요구된 **제출서류 목록** vs 사용자가 준비한 **실제 파일들** 을 자동 매칭해서
**무엇이 빠졌는지 / 유효기간이 지났는지** 를 체크리스트로 표시.

기획안 5.2 / 8.2 의 내용을 그대로 구현.

---

## 2. 파이프라인

```
RFP PDF/HWPX
      │
      ▼
┌────────────────────────┐
│  rfp_extractor         │ Gemini document-processing (TODO)
│   ↳ list[RequiredDocument] │
└──────────┬──────────────┘
           │
           │    + 사용자 폴더
           │      (예: D:\제출서류\)
           ▼
┌────────────────────────┐
│  matcher.build_checklist│
│   파일명 패턴 1차 매치    │
│   발행일 날짜 추출        │
└──────────┬──────────────┘
           │
           │  매칭 실패한 파일만
           ▼
┌────────────────────────┐
│  ocr_fallback (선택)   │ Tesseract (TODO, v0.6.0 본편)
│   PDF 첫 페이지 OCR     │
│   → 발행일 재시도         │
└──────────┬──────────────┘
           │
           ▼
┌────────────────────────┐
│  ChecklistResult        │
│   OK / WARNING / MISSING │
│   제출 가능 여부 플래그    │
└──────────┬──────────────┘
           │
           ▼
   체크리스트 탭 UI
```

---

## 3. 데이터 모델 (이미 스캐폴딩됨)

```python
# src/checklist/models.py
@dataclass
class RequiredDocument:
    id: str                    # "business_reg"
    name: str                  # "사업자등록증 사본"
    is_required: bool = True
    max_age_days: int | None   # 90 → 3개월 이내
    filename_hints: list[str]  # ["사업자등록증", "business_registration"]
    description: str           # RFP 스니펫

@dataclass
class MatchedFile:
    path: Path
    size_bytes: int
    issued_date: date | None
    issued_source: str         # "filename" / "ocr" / "unknown"

@dataclass
class ChecklistItem:
    doc: RequiredDocument
    matches: list[MatchedFile]
    status: DocumentStatus     # OK / WARNING / MISSING / UNKNOWN
    warning_reason: str

@dataclass
class ChecklistResult:
    rfp_path: str
    folder_path: str
    items: list[ChecklistItem]
    # 편의: ok_count, warning_count, missing_count, is_submittable
```

---

## 4. 핵심 알고리즘

### 4.1 RFP 추출 (TODO, Gemini)

```python
from google import genai

prompt = """이 공고문에서 입찰 참가자가 제출해야 할 필수/선택 서류 목록을 JSON 으로 뽑아줘.
각 서류는: id, name, is_required, max_age_days (발급일 제한이 있으면), filename_hints, description"""

client = genai.Client(api_key=...)
resp = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=[{"parts": [
        {"file_data": {"mime_type": "application/pdf", "file_uri": upload_pdf(rfp_path)}},
        {"text": prompt}
    ]}],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=...,   # RequiredDocument 배열
    )
)
```

비용: Gemini 3 기준 PDF 텍스트 추출 **무료**. 구조화 출력 토큰만 과금 (~₩10).

### 4.2 파일명 매치 (결정론, 이미 구현)

기획안 8.2 의 날짜 패턴:

```python
PATTERNS = [
    r"_(\d{8})\."            # _20260315.
    r"_(\d{4}-\d{2}-\d{2})"  # _2026-03-15
    r"_(\d{4}\.\d{2}\.\d{2})"# _2026.03.15
    r"(\d{6})_"              # 260315_
]
```

키워드 매치는 `filename_hints` 의 문자열을 파일명에 normalize (공백/언더스코어 제거,
소문자) 해서 포함 여부 확인.

### 4.3 OCR Fallback (TODO)

파일명에 발행일 없는 PDF는:

1. `pdfplumber` 로 첫 페이지 텍스트 추출 시도 (무료, 빠름)
2. 텍스트 없으면(스캔본) Tesseract OCR (`kor.traineddata`)
3. 추출된 텍스트에서 `20XX년 XX월 XX일` 또는 `YYYY-MM-DD` 패턴 탐색

의존성: `pdfplumber` (BSD), `pytesseract` + Tesseract 바이너리 (Apache). 둘 다 상업 OK.

Tesseract 는 PyInstaller 로 번들 불가 → **옵션 기능** 으로 표기. 사용자가 별도 설치.

---

## 5. UI 전략

새 **"체크리스트" 탭** (6번째 탭):

```
┌─ 체크리스트 ────────────────────────────┐
│ RFP:    [파일 선택...] 농정원_공고.pdf   │
│ 폴더:   [폴더 선택...] D:\제출서류\       │
│ [ RFP 분석 ]                              │
│                                           │
│ ─ 분석 결과 ─                             │
│ ✅ 사업자등록증 사본                      │
│    └ 사업자등록증_240815.pdf              │
│ ⚠️  법인 인감증명서 (발행일 150일 전)      │
│    └ 법인인감_251120.pdf                  │
│    └ 3개월 이내 요구됨                    │
│ ❌ 재무제표 — 파일 없음                    │
│ ✅ 정성제안서                              │
│ ❌ 정량제안서 — 파일 없음                  │
│                                           │
│ 요약: 2 OK / 1 WARNING / 2 MISSING        │
│ 상태: 제출 불가 (필수 서류 부족)            │
│ [ 보고서 저장... ]                        │
└───────────────────────────────────────────┘
```

각 항목은 클릭 시 RFP 원문 스니펫 표시 (왜 이 서류가 필요한지).

---

## 6. 사용자에게 부탁드리는 것

### 최소

- **실제 RFP PDF 3~5 종**
  - 농정원, 교육청, 중앙부처 등
  - 간단한 1~2페이지 공고 ~ 복잡한 30페이지 규격서 섞어서
- **같은 RFP 의 완성 제출 폴더 예시** (파일명 네이밍 컨벤션 확인용)

### 업로드 위치

`tests/fixtures/rfp_samples/` 폴더에 두시면 다음 세션에서 분석 시작.

---

## 7. v0.6.0 본편 스코프 (예상 3~4 주)

- **W1**: Gemini document-processing 통합 + 구조화 추출 스키마 확정
- **W2**: OCR fallback (Tesseract 연동) + 파일명 패턴 보강
- **W3**: 체크리스트 탭 UI + 보고서 출력 (HTML/PDF)
- **W4**: 엣지 케이스 (비정형 RFP, 스캔본) + 릴리즈

---

## 8. 위험 요소

| 위험 | 대응 |
|---|---|
| RFP 형식이 천차만별 (PDF / HWP / HWPX) | Gemini document-processing 이 PDF/DOCX/XLSX/PPTX 지원. HWP/HWPX 는 먼저 PDF 로 변환 후 처리 (한컴 Automation 배제) |
| 스캔본 PDF 는 텍스트 추출 안 됨 | Tesseract 한국어 OCR fallback |
| 파일명 네이밍 관습이 기관마다 다름 | `filename_hints` 리스트 + 사용자 정정 UI |
| 발행일이 파일명에도 내용에도 없음 | WARNING 상태로 두고 사용자 수동 확인 요청 |
| Tesseract 바이너리는 PyInstaller 번들 어려움 | 옵션 기능 — 사용자가 별도 설치. 없으면 WARNING 상태 |

---

**현재 foundation 상태**: `src/checklist/` 에 models/filename_matcher/rfp_extractor(stub)/matcher 완료.
본편 시작은 사용자가 RFP 샘플을 `tests/fixtures/rfp_samples/` 에 올려주신 뒤.
