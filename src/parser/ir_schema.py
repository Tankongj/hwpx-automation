"""중간표현(Intermediate Representation) 스키마.

원고 파서의 출력이자 HWPX 생성기의 입력인 :class:`Block` 을 정의한다.

레벨 정책
---------
- ``-1`` : 문서 제안서 제목(표지)
- ``0``  : 본문 (직전 제목 하위)
- ``1``  : Ⅰ. 대장 (Ctrl+1)
- ``2``  : 1 절 (Ctrl+2)
- ``3``  : 1) 소절 (Ctrl+3)
- ``4``  : (1) 단락 제목 (Ctrl+4)
- ``5``  : ① 원숫자 (Ctrl+5)
- ``6``  : □ 대주제 (Ctrl+6)
- ``7``  : ❍ 중주제 (Ctrl+7)
- ``8``  : - 하이픈 (Ctrl+8)
- ``9``  : · 가운뎃점 (Ctrl+9)
- ``10`` : * 주석/참고 (Ctrl+0)

v1 엔진(``src.hwpx.md_to_hwpx``)은 내부적으로 H1~H5 / L1~L4 / note / body 문자열 타입을
쓰므로 양방향 매핑 헬퍼를 함께 제공한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Level constants
# ---------------------------------------------------------------------------

LEVEL_TITLE = -1
LEVEL_BODY = 0
LEVEL_MIN = 1
LEVEL_MAX = 10

# level → v1 paragraph "type" 문자열
LEVEL_TO_V1_TYPE: dict[int, str] = {
    LEVEL_TITLE: "title",
    LEVEL_BODY: "body",
    1: "H1",
    2: "H2",
    3: "H3",
    4: "H4",
    5: "H5",
    6: "L1",
    7: "L2",
    8: "L3",
    9: "L4",
    10: "note",
}

V1_TYPE_TO_LEVEL: dict[str, int] = {v: k for k, v in LEVEL_TO_V1_TYPE.items()}
# v1 에는 title 대신 파일 내부에서 H1 만 쓰지만, v2 IR 쪽은 표지를 분리해 보존한다.
V1_TYPE_TO_LEVEL.setdefault("empty", LEVEL_BODY)


# ---------------------------------------------------------------------------
# Block
# ---------------------------------------------------------------------------

@dataclass
class Block:
    """원고 한 줄에 해당하는 IR 블록."""

    level: int
    text: str
    symbol: str = ""
    raw_line: str = ""
    line_no: int = 0
    ambiguous: bool = False
    parent_level: Optional[int] = None
    # 자유 확장: 특정 파서/리졸버가 덧붙이는 힌트 (예: Gemini 해석 reason)
    meta: dict = field(default_factory=dict)

    # ---- 편의 속성 ----
    @property
    def is_title(self) -> bool:
        return self.level == LEVEL_TITLE

    @property
    def is_body(self) -> bool:
        return self.level == LEVEL_BODY

    @property
    def is_heading(self) -> bool:
        return 1 <= self.level <= 5

    @property
    def is_bullet(self) -> bool:
        return 6 <= self.level <= 10

    # ---- v1 호환 ----
    @property
    def v1_type(self) -> str:
        """v1 paragraph dict 의 'type' 값.

        설계 메모: ``title`` (level -1) 은 v1 엔진이 알아듣는 타입이 아니므로 ``H1`` 으로
        위장한다. 이는 reference document 모드를 쓰지 않는 기본 경로에서 문서 제목을 첫 H1
        단락으로 렌더링하기 위한 일시 매핑이다. 공고 양식 업로드 시에는 별도의 표지 처리를
        쓸 예정 (W4).
        """
        if self.level == LEVEL_TITLE:
            return "H1"
        return LEVEL_TO_V1_TYPE.get(self.level, "body")

    def to_v1_dict(self) -> dict:
        """v1 ``md_to_hwpx`` 가 기대하는 ``{'type', 'text'}`` dict 로 변환."""
        return {"type": self.v1_type, "text": self.text}

    @classmethod
    def from_v1_dict(cls, data: dict, *, line_no: int = 0) -> "Block":
        t = data.get("type", "body")
        level = V1_TYPE_TO_LEVEL.get(t, LEVEL_BODY)
        return cls(
            level=level,
            text=str(data.get("text", "")),
            raw_line=str(data.get("text", "")),
            line_no=line_no,
        )


# ---------------------------------------------------------------------------
# Bulk helpers
# ---------------------------------------------------------------------------

def blocks_to_v1_paragraphs(blocks: list[Block]) -> list[dict]:
    """IR blocks → v1 paragraphs (리스트 단위 변환)."""
    return [b.to_v1_dict() for b in blocks]


def v1_paragraphs_to_blocks(paragraphs: list[dict]) -> list[Block]:
    """v1 paragraphs → IR blocks."""
    return [Block.from_v1_dict(p, line_no=i) for i, p in enumerate(paragraphs)]


__all__ = [
    "Block",
    "LEVEL_TITLE",
    "LEVEL_BODY",
    "LEVEL_MIN",
    "LEVEL_MAX",
    "LEVEL_TO_V1_TYPE",
    "V1_TYPE_TO_LEVEL",
    "blocks_to_v1_paragraphs",
    "v1_paragraphs_to_blocks",
]
