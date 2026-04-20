# 정량제안서 모드 설계 (v0.5.0 예정)

**상태**: foundation scaffold 완료 (`src/quant/`). 본편 구현은 **사용자 샘플 입수 후**.

---

## 1. 문제 정의

정량제안서는 정성제안서와 **구조가 완전히 다름**:

| 정성 (현재 지원) | 정량 (v0.5.0) |
|---|---|
| 자유 서술 + 계층(Ⅰ/1/1)/(1)/①/□/❍/-/·/※) | 고정된 서식 ``[서식 1~N]`` 양식 |
| `.txt` 원고 + 템플릿 | HWPX 템플릿 + 사용자 입력 값 |
| 파서가 **계층 추론** | 파서가 **필드 추출** |
| 출력 = 새 HWPX 생성 | 출력 = 템플릿 셀 채우기 |

기존 정성 파이프라인을 재활용할 수 없으므로 **평행 트랙**이 필요.

---

## 2. 데이터 모델 (이미 스캐폴딩됨)

```python
# src/quant/models.py
class FieldType(Enum):
    TEXT, MULTILINE, NUMBER, DATE, SELECT, CHECK, TABLE_ROW

@dataclass
class QuantField:
    id: str
    label: str                # "대표자명"
    field_type: FieldType
    required: bool = True
    hint: str = ""
    default: Any | None = None
    choices: list[str] = []   # SELECT 전용
    unit: str = ""             # NUMBER 전용 ("명", "원")
    hwpx_anchor: dict = {}     # 템플릿 내 위치 매핑

@dataclass
class QuantForm:
    id: str                   # "form_1"
    label: str                # "[서식 1] 기관 일반현황"
    fields: list[QuantField]
    repeat_min: int = 0       # TABLE_ROW 최소 개수
    repeat_max: int | None

@dataclass
class QuantProposal:
    template_path: str
    forms: list[QuantForm]
    values: dict[str, Any]    # "form_1.ceo_name" → "홍길동"
```

---

## 3. 파이프라인

```
정량제안서 HWPX 템플릿
          │
          ▼
┌────────────────────────┐
│  quant.parser.parse_   │  TODO
│    template            │
│   ↳ QuantProposal       │
└──────────┬──────────────┘
           │ (빈 values)
           ▼
┌────────────────────────┐
│  정량 탭 UI (신규)      │  TODO (PySide6 QFormLayout + QTable)
│   ↳ 사용자 입력         │
└──────────┬──────────────┘
           │ (채워진 values)
           ▼
┌────────────────────────┐
│  quant.converter.      │  TODO
│    convert             │
│   ↳ HWPX 파일           │
└──────────┬──────────────┘
           │
           ▼
   fix_namespaces + verify (재사용)
```

---

## 4. 파서 전략 (parser.py 의 TODO)

### 단계 1: 결정론 스캔

1. HWPX ZIP 열어 section0, section1... 수집
2. 각 section XML 순회:
   - `<hp:p>` 안의 텍스트에서 `[서식 N]` 또는 `서식 N:` 패턴 찾음 → `QuantForm` 시작
   - 바로 다음 `<hp:tbl>` 의 `<hp:tr>` × `<hp:tc>` 를 스캔
3. 각 `<hp:tc>` 분석:
   - **레이블 셀** (일반적으로 `charPrIDRef` 가 굵은 글씨 + 좌측 열) → `field.label`
   - **입력 셀** (빈 텍스트 또는 밑줄 배경) → `field.hwpx_anchor` 에 `(table_id, row, col)` 저장
4. 레이블 텍스트 패턴으로 `FieldType` 추론:
   - "성명" / "명칭" / "주소" → `TEXT`
   - "○○명" / "건" / "년" → `NUMBER` (단위는 unit 에)
   - "YYYY-MM-DD" / "년월일" → `DATE`
   - 체크박스 심볼(☐ ☑) → `CHECK`

### 단계 2: Gemini 보조 (선택)

복잡한 표(병합 셀, 다층 헤더) 는 결정론 파서가 놓칠 수 있음. 1회 Gemini 호출로:

```
프롬프트: "다음 HWPX section 의 각 셀을 QuantField 로 매핑해줘.
각 필드는 JSON 으로 {form_id, field_id, label, field_type, hwpx_anchor} 형식."
```

비용 ~₩10 (기존 resolver 재사용).

### 단계 3: 사용자 보정

파서가 뽑은 `QuantProposal` 을 **Settings 확장 탭**에서 사용자가 편집 가능.
- 필드 이름 바꾸기
- 타입 변경
- 필수/선택 토글
- 삭제/추가

편집된 스키마는 템플릿 메타데이터(index.json) 에 저장 → 재사용.

---

## 5. UI 전략

새 **"정량" 탭** 추가 (5번째 탭):

```
┌─ 정량 ──────────────────────────────────┐
│ 템플릿: [서식_2026_농정원        ▾]     │
│                                         │
│ ┌─ [서식 1] 기관 일반현황 ────────────┐│
│ │ 기관명:      ___________________     ││
│ │ 대표자명:    ___________________     ││
│ │ 설립년도:    ____ 년                 ││
│ │ 총원:        ____ 명                 ││
│ └─────────────────────────────────────┘│
│ ┌─ [서식 2] 주요 사업 실적 (3년) ─────┐│
│ │ 연도   사업명     금액    발주처      ││
│ │ ____  ________   ____   _______    ××│
│ │ ____  ________   ____   _______    ××│
│ │                          [+ 행 추가]││
│ └─────────────────────────────────────┘│
│                                         │
│ [ 변환 실행 ]  [ 미리보기 탭으로 ]      │
└─────────────────────────────────────────┘
```

Form 별 `QGroupBox` + `QFormLayout`. TABLE_ROW 는 `QTableWidget` + "+ 행 추가" 버튼.
값 변경 즉시 `QuantProposal.values` 에 반영.

---

## 6. 사용자에게 부탁드리는 것 (이 단계가 끝나야 본편 진입 가능)

본편 구현을 위해 **실제 정량제안서 HWPX 샘플** 필요:

### 최소 요구

- 기관 공고의 정량제안서 **빈 템플릿 HWPX 3~5 종**
  - 농정원, 교육청, 지자체, 중앙부처 등 다양한 발주처
  - 규모가 다른 것 섞어서 (간단한 1~2 페이지 vs 복잡한 10+ 페이지)

### 더 좋은 건

- 동일 템플릿의 **완성본** 도 함께 (필드가 실제 어떤 데이터 받는지 이해)
- **사용자가 겪은 불편함** 메모 (어느 셀이 특히 귀찮은지)

### 샘플 업로드 위치

`tests/fixtures/quant_samples/` 폴더에 두시면 제가 다음 세션에서 분석 시작합니다.

---

## 7. v0.5.0 본편 스코프 (예상 3~4 주)

- **W1**: 파서 (결정론 + Gemini 보조). 3~5 종 샘플 모두 통과하는 수준까지
- **W2**: 정량 탭 GUI (form/table 입력 위젯, 값 바인딩)
- **W3**: converter (채우기 + 행 복제) + verify
- **W4**: 사용자 보정 기능 + 템플릿별 스키마 캐싱 + 릴리즈

---

## 8. 위험 요소

| 위험 | 대응 |
|---|---|
| 기관마다 서식이 너무 다양함 | 결정론 파서 + Gemini 보조 조합, 사용자 보정 필수 |
| 병합 셀 / 표 안의 표 | Gemini 보조 + `hwpx_anchor` 구조체로 유연하게 표현 |
| 값 타입 추론 실패 | 기본 TEXT → 사용자가 설정 탭에서 수동 변경 가능 |
| 기존 정성 파이프라인과 충돌 | `src/quant/` 로 완전 분리, 같은 엔진(md_to_hwpx) 호출은 안 함 |

---

**현재 foundation 상태**: `src/quant/__init__.py`, `models.py`, `parser.py` (stub), `converter.py` (stub) 완료.
본편 시작은 사용자가 샘플을 `tests/fixtures/quant_samples/` 에 올려주신 뒤.
