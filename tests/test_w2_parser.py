"""W2: regex_parser 의 결정론 매핑 + ambiguous 마킹 검증.

길이 불변(length-agnostic) 설계 — 고정 문자 수 대신 구조적 속성을 assert.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.parser import regex_parser
from src.parser.ir_schema import LEVEL_BODY, LEVEL_TITLE, Block


FIXTURE = Path(__file__).parent / "fixtures" / "2026_귀농귀촌아카데미_원고.txt"


def parse_str(text: str) -> list[Block]:
    return regex_parser.parse(text)


# ---------------------------------------------------------------------------
# Deterministic rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line,expected_level,expected_symbol",
    [
        ("# Ⅰ. 기관현황",            1,  "#"),
        ("## 1. 일반현황",            2,  "##"),
        ("### 1) 제안사 현황",        3,  "###"),
        ("(1) 기관 개요",             4,  "(1)"),
        ("① 2023년 실적",            5,  "①"),
        ("□ 대주제입니다",            6,  "□"),
        ("❍ 중주제입니다",            7,  "❍"),
        ("○ 중주제 원형",             7,  "○"),
        ("- 하이픈 글머리",           8,  "-"),
        ("· 가운뎃점",                9,  "·"),
        ("※ 주석입니다",             10,  "※"),
        ("* 주석 별표",              10,  "*"),
    ],
)
def test_single_line_level(line, expected_level, expected_symbol):
    blocks = parse_str(line)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.level == expected_level
    assert b.symbol == expected_symbol


def test_title_is_extracted_only_at_top():
    text = (
        "# 2026년 제안서\n"
        "\n"
        "# Ⅰ. 첫 장\n"
    )
    blocks = parse_str(text)
    assert blocks[0].level == LEVEL_TITLE
    assert blocks[0].text == "2026년 제안서"
    assert blocks[1].level == 1
    assert blocks[1].text == "Ⅰ. 첫 장"


def test_second_non_roman_hash_is_not_title():
    """비-로마숫자 # 이 문서 중간에 또 나오면 title 이 아니라 body/기타로 취급."""
    text = "# Ⅰ. 첫 장\n\n# 그냥 제목?\n"
    blocks = parse_str(text)
    # first Ⅰ is level 1
    assert blocks[0].level == 1
    # second "# 그냥 제목?" doesn't match `^#\s+[Ⅰ...]` → falls through to body
    # (제목1 으로 승격하려면 Roman 이 필요) — 본문으로 처리됨
    assert all(b.level != LEVEL_TITLE for b in blocks[1:])


def test_skip_lines():
    text = "---\n```\n\n> quote\n"
    blocks = parse_str(text)
    assert blocks == []


def test_empty_input():
    assert parse_str("") == []


def test_parse_file_strips_utf8_bom(tmp_path):
    """UTF-8 BOM 이 있어도 첫 줄이 사라지지 않아야 한다 (Windows 메모장 기본 저장)."""
    target = tmp_path / "bom.txt"
    target.write_bytes(b"\xef\xbb\xbf# \xe2\x85\xa0. \xec\x9e\xa5\n")  # BOM + "# Ⅰ. 장"
    blocks = regex_parser.parse_file(target)
    assert len(blocks) == 1
    assert blocks[0].level == 1
    assert "Ⅰ" in blocks[0].text


def test_parent_level_chain():
    text = (
        "# Ⅰ. 장\n"
        "## 1. 절\n"
        "### 1) 소절\n"
        "(1) 단락\n"
        "- 본문성 하이픈\n"
    )
    blocks = parse_str(text)
    # title 없음 → chain: 1 → parent None, 2 → 1, 3 → 2, 4 → 3, 8 → 4
    by_level = {b.level: b for b in blocks}
    assert by_level[1].parent_level is None
    assert by_level[2].parent_level == 1
    assert by_level[3].parent_level == 2
    assert by_level[4].parent_level == 3
    assert by_level[8].parent_level == 4


# ---------------------------------------------------------------------------
# Ambiguity marking
# ---------------------------------------------------------------------------

def test_long_bullet_marks_ambiguous():
    long_body = "가" * 80  # 80 chars
    text = f"□ {long_body}"
    blocks = parse_str(text)
    assert blocks[0].level == 6
    assert blocks[0].ambiguous is True


def test_short_symboled_line_not_ambiguous():
    text = "□ 짧은 제목"
    blocks = parse_str(text)
    assert blocks[0].level == 6
    assert blocks[0].ambiguous is False


def test_short_bare_noun_phrase_marked_ambiguous_as_body():
    """기호 없이 짧은 명사구 — 제목 후보이므로 ambiguous."""
    text = "요약 테이블"  # 짧음, 종결어 아님
    blocks = parse_str(text)
    assert blocks[0].level == LEVEL_BODY
    assert blocks[0].ambiguous is True


def test_long_plain_sentence_not_ambiguous():
    """종결어(ㅇㅁ/다/임 등) 로 끝나는 긴 문장은 그냥 본문."""
    text = "본 사업은 귀농귀촌 아카데미 운영을 목적으로 함"
    blocks = parse_str(text)
    assert blocks[0].level == LEVEL_BODY
    assert blocks[0].ambiguous is False


def test_ambiguous_blocks_filter():
    text = "□ 짧음\n□ " + ("가" * 100)
    amb = regex_parser.ambiguous_blocks(parse_str(text))
    assert len(amb) == 1
    assert amb[0].level == 6


# ---------------------------------------------------------------------------
# Real fixture (length-agnostic structural asserts)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FIXTURE.exists(), reason="sample fixture not present")
def test_fixture_parses_and_produces_all_needed_levels():
    blocks = regex_parser.parse_file(FIXTURE)

    # 문서가 비어 있지 않아야 한다
    assert len(blocks) > 0

    levels_present = {b.level for b in blocks}

    # 기본 제안서 템플릿에는 최소한 title, H1, H2, H3, (1) level 4, ① level 5 가 나와야 한다
    for required in (LEVEL_TITLE, 1, 2, 3, 4, 5):
        assert required in levels_present, f"level {required} 이 파싱 결과에 없음"

    # 각 블록은 본문이거나 어느 규칙에 매치된 상태(raw_line 이 비어 있지 않음)
    assert all(b.raw_line for b in blocks)

    # 제목은 정확히 1개만 나와야 한다
    assert sum(1 for b in blocks if b.level == LEVEL_TITLE) == 1

    # parent_level 은 title/root heading 제외 모두 값이 있어야 한다
    for b in blocks:
        if b.level in (LEVEL_TITLE, 1):
            continue
        assert b.parent_level is not None, (
            f"line {b.line_no} level {b.level} 에 parent_level 이 할당되지 않음: {b.text!r}"
        )


@pytest.mark.skipif(not FIXTURE.exists(), reason="sample fixture not present")
def test_fixture_ambiguity_within_plausible_range():
    """ambiguous 비율은 0~40% 범위에서만 유효 (과도/과소 마킹 방지)."""
    blocks = regex_parser.parse_file(FIXTURE)
    total = len(blocks)
    amb = len(regex_parser.ambiguous_blocks(blocks))
    ratio = amb / total
    assert 0.0 <= ratio <= 0.4, f"ambiguous 비율 {ratio:.2%} 이 비정상 범위 (총 {total}, 애매 {amb})"
