"""기호 기반 결정론 원고 파서.

기획안 4.1 의 ``RULES`` 테이블을 구현한다. 원고 한 줄씩 읽어 기호 → 레벨을 결정론적으로
매핑하고, 애매한 줄은 ``ambiguous=True`` 로 표시해 Gemini 해석기(W3) 가 나중에 채우게
남긴다.

입력
----
UTF-8 텍스트 (파일 경로 또는 문자열).

출력
----
``list[Block]`` — :mod:`src.parser.ir_schema`

설계 원칙
---------
1. **결정론 우선**. Gemini 호출 없이 90 %+ 결정되도록 규칙을 충분히 세분화.
2. **길이 불변**. 원고가 100 줄이든 10,000 줄이든 같은 복잡도로 동작 (단순 선형 스캔).
3. **관대한 입력**. 선두 공백, 여러 기호 변형(○/◯/❍, -/—/–) 허용.
4. **body 에는 ambiguous 표시 없음**. 애매 판정은 "기호가 붙었지만 실제로는 본문인가?"
   같은 모호 케이스에만 적용.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Pattern, Sequence, Union

from .ir_schema import LEVEL_BODY, LEVEL_TITLE, Block


PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# Rule table (plan 4.1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """한 레벨을 결정하는 정규식 규칙."""

    level: int
    pattern: Pattern[str]
    symbol_label: str
    note: str = ""


# 주의: 순서가 의미를 가진다. 첫 매칭이 채택되므로 더 구체적인 것을 위로.
RULES: list[Rule] = [
    # 마크다운 제목 (level 1~3)
    # 문서 제목(로마숫자 없는 #)은 별도 처리 (아래 TITLE_PATTERN)
    Rule(1, re.compile(r"^#(?!#)\s+[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ].*$"), "# Ⅰ.", "마크다운 H1 + 로마숫자 → 대장"),
    Rule(2, re.compile(r"^##(?!#)\s+.+$"),                "##",     "마크다운 H2 → 1. 절"),
    Rule(3, re.compile(r"^###(?!#)\s+.+$"),               "###",    "마크다운 H3 → 1) 소절"),

    # 마크다운 #### / ##### 도 허용 (v1 문서 호환)
    Rule(4, re.compile(r"^####(?!#)\s+.+$"),              "####",   "마크다운 H4 (호환)"),
    Rule(5, re.compile(r"^#####\s+.+$"),                  "#####",  "마크다운 H5 (호환)"),

    # 비 마크다운 선두 기호
    Rule(4, re.compile(r"^\s*\(\d+\)\s+.+$"),             "(1)",    "(1) 단락 제목"),
    Rule(5, re.compile(r"^\s*[\u2460-\u2473]\s*.+$"),     "①",      "원숫자 항목"),
    Rule(6, re.compile(r"^\s*□\s*.+$"),                   "□",      "대주제"),
    Rule(7, re.compile(r"^\s*[❍○◯]\s*.+$"),              "❍",      "중주제"),
    Rule(8, re.compile(r"^\s*-\s+.+$"),                   "-",      "하이픈 글머리"),
    Rule(9, re.compile(r"^\s*·\s*.+$"),                   "·",      "가운뎃점"),
    Rule(10, re.compile(r"^\s*[*※]\s*.+$"),               "*",      "주석/참고"),
]

# 문서 제목: 원고 최상단, 로마숫자 없이 #. 한 번만 매치.
TITLE_PATTERN: Pattern[str] = re.compile(r"^#\s+(?![ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]).+$")

# 무시 라인 (빈 줄, 구분선, 코드 펜스, 인용)
SKIP_PATTERNS: list[Pattern[str]] = [
    re.compile(r"^\s*$"),
    re.compile(r"^---+$"),
    re.compile(r"^```"),
    re.compile(r"^\s*>\s"),
]


# ---------------------------------------------------------------------------
# Heuristics for ambiguity
# ---------------------------------------------------------------------------

# 기획안 4.1 애매 케이스:
#   - 기호 매치됐으나 본문 길이가 50자 이상인 긴 줄
#   - 연속된 레벨 점프 (1 → 5 같은 급격한 점프)
#   - 기호 없는 짧고 명사구로 보이는 줄 (레벨 1~4 제목 후보)
AMBIGUOUS_LONG_THRESHOLD = 50
LEVEL_JUMP_THRESHOLD = 2  # 직전 제목 레벨 대비 ``+3 이상`` 증가를 점프로 본다
SHORT_BODY_TITLE_CANDIDATE_MAX = 30  # 짧은 본문 + 종결어 아닌 경우 제목 후보
SENTENCE_ENDINGS = ("임", "음", "함", "됨", ".", "다", "요")


def _strip_symbol(level: int, raw: str) -> tuple[str, str]:
    """선두 기호와 나머지 텍스트를 분리한다.

    반환: ``(symbol, text)`` — ``symbol`` 은 기호 원문(공백 제외), ``text`` 는 본문.
    level 0(body)/level -1(title) 은 symbol 빈 문자열.
    """
    line = raw
    # 선두 공백 제거 (마크다운 제목 외)
    if level in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
        # 마크다운 헤더는 # 을 기호로 남긴다
        m = re.match(r"^(#{1,5})\s+(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*(\(\d+\))\s+(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*([\u2460-\u2473])\s*(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*(□)\s*(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*([❍○◯])\s*(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*(-)\s+(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*(·)\s*(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

        m = re.match(r"^\s*([*※])\s*(.*)$", raw)
        if m:
            return m.group(1), m.group(2).strip()

    return "", raw.strip()


def _is_title_candidate(text: str) -> bool:
    """짧고 종결어로 끝나지 않는 명사구 느낌의 줄 → 제목 후보."""
    if not text:
        return False
    if len(text) > SHORT_BODY_TITLE_CANDIDATE_MAX:
        return False
    stripped = text.rstrip(" .,!?~")
    if not stripped:
        return False
    # 한글 종결어미로 끝나면 본문일 확률이 높음
    if stripped[-1] in SENTENCE_ENDINGS:
        return False
    return True


# ---------------------------------------------------------------------------
# Main parse
# ---------------------------------------------------------------------------

def _classify_line(raw_line: str) -> Optional[tuple[int, str, str]]:
    """한 줄 → ``(level, symbol, text)`` 또는 ``None`` (무시 라인)."""
    if any(p.match(raw_line) for p in SKIP_PATTERNS):
        return None

    for rule in RULES:
        if rule.pattern.match(raw_line):
            symbol, text = _strip_symbol(rule.level, raw_line)
            return rule.level, symbol, text

    # 어떤 규칙에도 안 걸림 → body
    return LEVEL_BODY, "", raw_line.strip()


def parse(
    text: str,
    *,
    ambiguous_long_threshold: int = AMBIGUOUS_LONG_THRESHOLD,
) -> list[Block]:
    """원고 텍스트 → IR Block 리스트.

    Parameters
    ----------
    text : 원고 내용 (UTF-8 문자열)
    ambiguous_long_threshold : 기호 매치됐을 때 본문 길이가 이 값 이상이면 ambiguous 로
        마킹. 기본 50. 낮추면 더 많이 ambiguous 로 보고 Gemini 호출 토큰이 늘어난다.

    Returns
    -------
    list[Block]
    """
    blocks: list[Block] = []
    title_assigned = False
    last_heading_level: Optional[int] = None

    lines = text.splitlines()
    for idx, raw in enumerate(lines):
        # 문서 제목(최상단 1회) 우선 처리
        if not title_assigned and TITLE_PATTERN.match(raw):
            # 단, 앞에 이미 다른 heading/body 블록이 있으면 title 로 안 본다.
            if not any(b.level != LEVEL_BODY for b in blocks):
                _, text_val = _strip_symbol(1, raw)  # # 제거
                blocks.append(
                    Block(
                        level=LEVEL_TITLE,
                        text=text_val,
                        symbol="#",
                        raw_line=raw,
                        line_no=idx,
                    )
                )
                title_assigned = True
                continue

        cls = _classify_line(raw)
        if cls is None:
            continue

        level, symbol, text_val = cls
        if not text_val:
            # symbol 만 있고 본문이 없는 경우 무시
            continue

        ambiguous = False

        # (a) 기호 매치됐는데 본문이 이상하게 긴 경우
        if 1 <= level <= 10 and len(text_val) >= ambiguous_long_threshold:
            ambiguous = True

        # (b) 급격한 레벨 점프 (이전 제목 대비 +LEVEL_JUMP_THRESHOLD 초과)
        if 1 <= level <= 5 and last_heading_level is not None:
            if level - last_heading_level > LEVEL_JUMP_THRESHOLD:
                ambiguous = True

        # (c) 기호 없는 짧은 명사구 → 제목 후보 (body 인데 제목일 수도)
        if level == LEVEL_BODY and _is_title_candidate(text_val):
            ambiguous = True

        block = Block(
            level=level,
            text=text_val,
            symbol=symbol,
            raw_line=raw,
            line_no=idx,
            ambiguous=ambiguous,
        )
        blocks.append(block)

        if 1 <= level <= 5:
            last_heading_level = level

    _assign_parent_levels(blocks)
    return blocks


def parse_file(
    path: PathLike,
    encoding: str = "utf-8",
    *,
    ambiguous_long_threshold: int = AMBIGUOUS_LONG_THRESHOLD,
) -> list[Block]:
    """파일 경로 버전. UTF-8 BOM 이 있으면 제거한다 (Windows 메모장 기본 저장)."""
    text = Path(path).read_text(encoding=encoding)
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return parse(text, ambiguous_long_threshold=ambiguous_long_threshold)


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _assign_parent_levels(blocks: Sequence[Block]) -> None:
    """각 블록의 ``parent_level`` 을 직전 상위 블록 레벨로 채움.

    body 는 가장 가까운 heading/bullet 의 레벨을 상속, heading 은 자기보다 작은 레벨을 상속.
    """
    stack: list[int] = []
    for b in blocks:
        if b.level == LEVEL_TITLE:
            stack = [LEVEL_TITLE]
            continue

        if b.level == LEVEL_BODY:
            if stack:
                b.parent_level = stack[-1]
            continue

        # heading/bullet: stack 을 자기 레벨보다 작은 것까지 pop
        while stack and stack[-1] >= b.level and stack[-1] != LEVEL_TITLE:
            stack.pop()
        if stack:
            b.parent_level = stack[-1]
        stack.append(b.level)


# ---------------------------------------------------------------------------
# Ambiguity helpers (Gemini resolver 연결용, W3)
# ---------------------------------------------------------------------------

def ambiguous_blocks(blocks: Iterable[Block]) -> list[Block]:
    """``ambiguous=True`` 인 블록만 추려 돌려준다."""
    return [b for b in blocks if b.ambiguous]


__all__ = [
    "Rule",
    "RULES",
    "TITLE_PATTERN",
    "SKIP_PATTERNS",
    "parse",
    "parse_file",
    "ambiguous_blocks",
]
