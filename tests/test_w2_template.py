"""W2: template_manager (APPDATA CRUD) + template_analyzer (styleMap 추출) 검증."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.template.default_10_levels import (
    DEFAULT_STYLE_MAP,
    V1_TYPE_STYLE_MAP,
    StyleSpec,
)
from src.template.template_analyzer import analyze
from src.template.template_manager import (
    DEFAULT_TEMPLATE_ID,
    TemplateManager,
    TemplateNotFoundError,
    default_template_dir,
)


BUNDLED_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "templates" / "00_기본_10단계스타일.hwpx"
)


# ---------------------------------------------------------------------------
# default_10_levels
# ---------------------------------------------------------------------------

def test_default_style_map_has_all_levels():
    for lv in range(1, 11):
        spec = DEFAULT_STYLE_MAP[lv]
        assert isinstance(spec, StyleSpec)
        assert spec.level == lv
        assert spec.font
        assert spec.size > 0


def test_v1_type_style_map_has_all_types():
    for t in ("H1", "H2", "H3", "H4", "H5", "L1", "L2", "L3", "L4", "note", "body", "empty"):
        assert t in V1_TYPE_STYLE_MAP
        ids = V1_TYPE_STYLE_MAP[t]
        assert ids.para and ids.char


# ---------------------------------------------------------------------------
# template_manager
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_library(tmp_path: Path) -> Path:
    """각 테스트가 깨끗한 라이브러리 디렉토리를 받도록."""
    return tmp_path / "templates"


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_ensure_initialized_creates_default(tmp_library: Path):
    mgr = TemplateManager(tmp_library)
    entries = mgr.list()
    assert len(entries) == 1
    e = entries[0]
    assert e.id == DEFAULT_TEMPLATE_ID
    assert e.is_default
    assert (tmp_library / e.file).exists()
    assert (tmp_library / "index.json").exists()


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_get_default_and_path(tmp_library: Path):
    mgr = TemplateManager(tmp_library)
    default = mgr.get_default()
    assert default.id == DEFAULT_TEMPLATE_ID
    path = mgr.get_path(default.id)
    assert path.exists()
    assert path.suffix.lower() == ".hwpx"


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_add_user_template(tmp_library: Path, tmp_path: Path):
    # 업로드할 가짜 HWPX 파일: 실제 번들 템플릿을 복사해서 다른 이름으로 업로드 시뮬레이션
    uploaded = tmp_path / "농정원_공고_양식.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, uploaded)

    mgr = TemplateManager(tmp_library)
    entry = mgr.add(uploaded, name="농정원 2026 공고양식", description="테스트 업로드")
    assert entry.id.startswith("user_")
    assert entry.name == "농정원 2026 공고양식"
    assert entry.is_default is False
    assert (tmp_library / entry.file).exists()

    # list 에 포함되어야 함
    ids = {e.id for e in mgr.list()}
    assert DEFAULT_TEMPLATE_ID in ids and entry.id in ids


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_remove_user_template(tmp_library: Path, tmp_path: Path):
    uploaded = tmp_path / "temp.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, uploaded)
    mgr = TemplateManager(tmp_library)
    entry = mgr.add(uploaded, name="임시")
    mgr.remove(entry.id)
    assert all(e.id != entry.id for e in mgr.list())


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_cannot_remove_default(tmp_library: Path):
    mgr = TemplateManager(tmp_library)
    with pytest.raises(ValueError):
        mgr.remove(DEFAULT_TEMPLATE_ID)


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_rejects_duplicate_name(tmp_library: Path, tmp_path: Path):
    """같은 name 으로 두 번 추가하려고 하면 ValueError."""
    uploaded = tmp_path / "first.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, uploaded)
    mgr = TemplateManager(tmp_library)
    mgr.add(uploaded, name="중복 이름")

    another = tmp_path / "second.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, another)
    with pytest.raises(ValueError, match="이미 같은 이름"):
        mgr.add(another, name="중복 이름")


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_manager_set_default_switches(tmp_library: Path, tmp_path: Path):
    uploaded = tmp_path / "temp.hwpx"
    shutil.copy2(BUNDLED_TEMPLATE, uploaded)
    mgr = TemplateManager(tmp_library)
    entry = mgr.add(uploaded, name="임시")
    mgr.set_default(entry.id)
    new_default = mgr.get_default()
    assert new_default.id == entry.id

    # 이전 기본은 해제돼야 함
    old = mgr.get(DEFAULT_TEMPLATE_ID)
    assert old.is_default is False


def test_manager_get_missing_raises(tmp_path: Path):
    mgr = TemplateManager(tmp_path, auto_init=False)
    with pytest.raises(TemplateNotFoundError):
        mgr.get("nonexistent")


def test_default_template_dir_under_appdata_or_home():
    d = default_template_dir()
    assert d.name == "templates"
    # Windows 에서는 APPDATA 하위, 없으면 home 하위
    assert "HwpxAutomation" in str(d) or ".hwpx-automation" in str(d)


# ---------------------------------------------------------------------------
# template_analyzer
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_analyzer_extracts_all_levels_from_bundled():
    sm = analyze(BUNDLED_TEMPLATE)
    # 레벨 0(body) 과 1~10 모두 채워져야 한다
    for lv in range(0, 11):
        assert lv in sm.level_to_ids, f"level {lv} missing"

    # 페이지 설정은 A4 15/15/20/20/10/10 (plan 4.4)
    ps = sm.page_setup
    assert ps.paper == "A4"
    assert ps.margin_top_mm == pytest.approx(15.0, abs=0.5)
    assert ps.margin_bottom_mm == pytest.approx(15.0, abs=0.5)
    assert ps.margin_left_mm == pytest.approx(20.0, abs=0.5)
    assert ps.margin_right_mm == pytest.approx(20.0, abs=0.5)


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_analyzer_name_heuristic_for_bundled():
    sm = analyze(BUNDLED_TEMPLATE)
    # 번들 템플릿에는 제목1~3, 본문1, □/❍/-/·/* 스타일이 이름으로 있다
    expected_names = {1: "제목1", 2: "제목2", 3: "제목3", 4: "본문1",
                      6: "□ 4칸", 7: "❍ 5칸", 8: "- 6칸", 9: "· 7칸", 10: "* 9칸"}
    for lv, name in expected_names.items():
        assert sm.level_to_name.get(lv) == name, (
            f"level {lv} name 매칭 실패: expected {name!r} got {sm.level_to_name.get(lv)!r}"
        )


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_analyzer_font_sizes_reasonable():
    sm = analyze(BUNDLED_TEMPLATE)
    # 상위 레벨은 큰 글씨, 하위는 작은 글씨
    assert sm.level_to_font_size_pt[1] >= sm.level_to_font_size_pt[3]
    assert sm.level_to_font_size_pt[4] >= sm.level_to_font_size_pt[10]


@pytest.mark.skipif(not BUNDLED_TEMPLATE.exists(), reason="bundled template missing")
def test_analyzer_to_engine_style_dict_has_all_types():
    sm = analyze(BUNDLED_TEMPLATE)
    ed = sm.to_engine_style_dict()
    for key in ("H1", "H2", "H3", "H4", "H5", "L1", "L2", "L3", "L4", "note", "body"):
        assert key in ed
        assert "para" in ed[key] and "char" in ed[key] and "style" in ed[key]


def test_analyzer_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        analyze(tmp_path / "no_such.hwpx")
