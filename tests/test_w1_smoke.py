"""W1 포팅 검증: 엔진 모듈이 import 되고 IR → HWPX → verify 파이프라인이 동작."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.hwpx import fix_namespaces, md_to_hwpx, verify_hwpx
from src.parser.ir_schema import (
    LEVEL_BODY,
    LEVEL_TITLE,
    Block,
    blocks_to_v1_paragraphs,
    v1_paragraphs_to_blocks,
)


# v1 프로젝트에서 가져오는 레퍼런스 템플릿 (로컬 개발 전용)
V1_TEMPLATE = Path(
    r"D:/03_antigravity/19_[26 귀농귀촌 아카데미]/hwpx-proposal-automation/templates/정성제안서 서식.hwpx"
)


def test_module_imports():
    assert hasattr(fix_namespaces, "fix_hwpx")
    assert hasattr(md_to_hwpx, "convert")
    assert hasattr(md_to_hwpx, "convert_markdown")
    assert hasattr(verify_hwpx, "verify")


def test_block_level_round_trip():
    block = Block(level=6, text="□ 대주제")
    assert block.v1_type == "L1"
    assert block.is_bullet

    via_v1 = v1_paragraphs_to_blocks(blocks_to_v1_paragraphs([block]))
    assert via_v1[0].level == 6
    assert via_v1[0].text == "□ 대주제"


def test_block_title_and_body_semantics():
    title = Block(level=LEVEL_TITLE, text="표지 제목")
    body = Block(level=LEVEL_BODY, text="본문")
    assert title.is_title and not title.is_heading
    assert body.is_body and not body.is_bullet


@pytest.mark.skipif(not V1_TEMPLATE.exists(), reason="v1 template not present on this machine")
def test_ir_to_hwpx_smoke(tmp_path: Path):
    blocks = [
        Block(level=1, text="Ⅰ. 사업 개요"),
        Block(level=2, text="1 추진 배경"),
        Block(level=3, text="추진 필요성"),
        Block(level=6, text="□ 귀농귀촌 아카데미 운영 역량 확보"),
        Block(level=0, text="본 사업은 귀농귀촌 인구 확산을 위한 종합 교육체계를 목표로 함."),
    ]

    out = tmp_path / "ir_smoke.hwpx"
    md_to_hwpx.convert(blocks, template=V1_TEMPLATE, output=out)

    assert out.exists()
    assert out.stat().st_size > 5_000

    report = verify_hwpx.verify(out, doc_type="qualitative")
    # Structural/common checks must all pass; content-level qualitative checks
    # are allowed to fail because the smoke IR is too short to score on them.
    structural = [c for c in report.checks if c.category in ("common", "advanced")]
    assert all(c.passed for c in structural), [
        (c.name, c.detail) for c in structural if not c.passed
    ]
