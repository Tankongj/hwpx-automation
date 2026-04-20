"""정량제안서 데이터 모델 — v0.5.0 foundation.

정성제안서 IR(:class:`~src.parser.ir_schema.Block`) 이 "계층 구조" 를 표현한다면,
정량제안서 IR 은 "서식별 필드 + 값" 을 표현한다.

개념
----
한 정량제안서는 여러 ``[서식 N]`` 블록의 나열. 각 서식은:
- 식별자 (예: "서식 1", "기관 일반현황")
- 고정된 표/문단 구조 (HWPX 의 section / table)
- 채울 수 있는 **필드** (셀 또는 문단의 placeholder)
- 메타데이터 (필수 여부, 힌트, 데이터 타입)

데이터 타입 (v0.5.0 enum)
- TEXT: 단일 줄 텍스트
- MULTILINE: 여러 줄 텍스트
- NUMBER: 숫자 (단위 포함 가능)
- DATE: 날짜 (YYYY-MM-DD)
- SELECT: 미리 정의된 선택지 중 하나
- CHECK: 체크/언체크
- TABLE_ROW: 표의 한 행 반복 (경력·실적 목록 등)

TODO (v0.5.0 본편)
------------------
1. 템플릿 파서: HWPX 의 특정 텍스트 패턴(``[서식 N]``, 색깔 배경 셀 등) 기반 필드 탐지
2. Gemini 보조: 복잡한 표 구조 → 필드 스키마 자동 추출 (1회 호출)
3. GUI: QTableWidget / QFormLayout 기반 입력 폼
4. 정성 원고에서 수치 자동 추출 (선택): "수료생 1,298명" 같은 값을 Gemini 가 원고에서 찾아 pre-fill
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FieldType(Enum):
    TEXT = "text"
    MULTILINE = "multiline"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    CHECK = "check"
    TABLE_ROW = "table_row"    # 가변 행 표 (경력/실적 목록)


@dataclass
class QuantField:
    """정량제안서의 한 필드.

    Parameters
    ----------
    id : form 내에서 유일한 식별자 (예: "ceo_name", "employee_count")
    label : UI 에 표시될 한글 라벨 (예: "대표자명", "총원")
    field_type : 입력 타입
    required : 필수 여부
    hint : placeholder / 도움말
    default : 기본값 (없으면 None)
    choices : SELECT 타입에만 의미 있음
    unit : 단위 (예: "명", "원") — NUMBER 타입에만
    hwpx_anchor : 템플릿 HWPX 에서 이 필드가 매핑되는 위치 식별자
        (v0.5.0 본편에서 structured — 예: table_id/row/col 또는 paragraph anchor)
    """

    id: str
    label: str
    field_type: FieldType = FieldType.TEXT
    required: bool = True
    hint: str = ""
    default: Optional[Any] = None
    choices: list[str] = field(default_factory=list)
    unit: str = ""
    hwpx_anchor: dict = field(default_factory=dict)


@dataclass
class QuantForm:
    """하나의 ``[서식 N]`` 블록."""

    id: str                    # 예: "form_1"
    label: str                 # 예: "서식 1: 기관 일반현황"
    description: str = ""
    fields: list[QuantField] = field(default_factory=list)
    # TABLE_ROW 필드의 행 반복을 허용하기 위한 최소/최대 개수
    repeat_min: int = 0
    repeat_max: Optional[int] = None


@dataclass
class QuantProposal:
    """전체 정량제안서 문서 — 여러 form 의 묶음."""

    template_path: str
    forms: list[QuantForm] = field(default_factory=list)
    # 사용자가 입력한 값. 키는 "form_id.field_id" 형식.
    # TABLE_ROW 는 "form_id.field_id[idx]" 형식으로 확장 (v0.5.0 본편 설계).
    values: dict[str, Any] = field(default_factory=dict)

    # ---- 편의 ----

    def get(self, form_id: str, field_id: str, default: Any = None) -> Any:
        return self.values.get(f"{form_id}.{field_id}", default)

    def set(self, form_id: str, field_id: str, value: Any) -> None:
        self.values[f"{form_id}.{field_id}"] = value

    def field_keys(self) -> list[str]:
        keys: list[str] = []
        for f in self.forms:
            for fld in f.fields:
                keys.append(f"{f.id}.{fld.id}")
        return keys

    def missing_required(self) -> list[tuple[str, str]]:
        """(form.id, field.id) 목록으로 미기입 필수 필드 반환."""
        missing: list[tuple[str, str]] = []
        for f in self.forms:
            for fld in f.fields:
                if not fld.required:
                    continue
                if not self.get(f.id, fld.id):
                    missing.append((f.id, fld.id))
        return missing


# ---------------------------------------------------------------------------
# v0.5.0 MVP: cell-level editor 모델
# ---------------------------------------------------------------------------

@dataclass
class QuantCell:
    """정량제안서 HWPX 내 한 셀의 위치 + 현재 텍스트.

    필드 타입 추론 없음 — 셀 단위로만 다루고 타입 해석은 사용자에게 맡긴다.

    Position semantics (v0.5.0):
        - ``para_index`` : section0.xml 의 top-level ``<hp:p>`` 인덱스
        - ``table_idx``  : **그 paragraph 내** 의 ``<hp:tbl>`` 순번 (0부터, 중첩 표 평탄화 X)
        - ``row`` / ``col`` : 해당 표의 행/열

    v0.5.1 추가:
        - ``row_span`` / ``col_span`` : 병합 셀 크기 (기본 1/1)
        - ``is_span_origin``          : 병합 셀의 좌상단 원점이면 True. 가려진 셀은 False.
    """

    form_id: str           # 예: "form_1" (= [서식 1])
    form_label: str        # 예: "[서식 1] 일반현황 및 연혁"
    para_index: int        # section0.xml 의 <hp:p> 인덱스
    table_idx: int         # 그 paragraph 내 <hp:tbl> 순번
    row: int               # 표 내 행 인덱스
    col: int               # 표 내 셀 인덱스
    text: str              # 현재 셀 텍스트 (모든 <hp:t> 의 text 합친 값)
    # form 전체에서 몇 번째 표인지 (UI 표시 용, parser 가 채움)
    form_table_ordinal: int = 0
    # v0.5.1: 병합 셀
    row_span: int = 1
    col_span: int = 1
    is_span_origin: bool = True

    @property
    def path(self) -> str:
        """UI 식별용 경로. 예: ``form_1/tbl3/r3c1`` (form 전체 ordinal 기반)."""
        return (
            f"{self.form_id}/tbl{self.form_table_ordinal}/r{self.row}c{self.col}"
        )

    @property
    def key(self) -> tuple[int, int, int, int]:
        return (self.para_index, self.table_idx, self.row, self.col)


@dataclass
class RowOp:
    """v0.5.1: 행 조작 지시.

    ``op`` = ``"duplicate"`` (source_row 복제해 뒤에 삽입) / ``"delete"`` (source_row 제거)
    converter 가 save_document 시점에 적용.
    """

    para_index: int
    table_idx: int
    source_row: int
    op: str = "duplicate"     # "duplicate" | "delete"


@dataclass
class QuantDocument:
    """정량제안서 전체 — 파싱 결과 + 편집 상태.

    기본 플로우:
        1. :func:`src.quant.parser.parse_document` 로 템플릿 로드 → 모든 셀 추출
        2. 사용자가 ``cells[i].text`` 편집 (+ v0.5.1: 행 추가/삭제 지시를 ``row_ops`` 에 쌓음)
        3. :func:`src.quant.converter.save_document` 로 원본 HWPX 에 쓰기
    """

    template_path: str
    cells: list[QuantCell] = field(default_factory=list)
    # form 순서 유지를 위해 form_id → label 매핑도 저장
    form_labels: dict[str, str] = field(default_factory=dict)
    # v0.5.1: 행 조작 지시들 (save 시점에 순서대로 적용)
    row_ops: list[RowOp] = field(default_factory=list)

    # ---- 조회 ----

    def forms(self) -> list[tuple[str, str]]:
        """(form_id, label) 목록 — 등록 순서."""
        return list(self.form_labels.items())

    def cells_of(self, form_id: str) -> list[QuantCell]:
        return [c for c in self.cells if c.form_id == form_id]

    def cells_of_table(self, form_id: str, ordinal: int) -> list[QuantCell]:
        """form 전체 ordinal 기준 한 표의 셀 목록."""
        return [
            c for c in self.cells
            if c.form_id == form_id and c.form_table_ordinal == ordinal
        ]

    def tables_of(self, form_id: str) -> list[int]:
        """해당 form 의 table ordinal 목록 (form_table_ordinal, 정렬)."""
        idxs = sorted({c.form_table_ordinal for c in self.cells if c.form_id == form_id})
        return idxs

    def table_shape(self, form_id: str, ordinal: int) -> tuple[int, int]:
        """(rows, cols) — max row/col + 1."""
        cs = self.cells_of_table(form_id, ordinal)
        if not cs:
            return (0, 0)
        return (max(c.row for c in cs) + 1, max(c.col for c in cs) + 1)


__all__ = [
    "FieldType",
    "QuantField",
    "QuantForm",
    "QuantProposal",
    "QuantCell",
    "QuantDocument",
    "RowOp",
]
