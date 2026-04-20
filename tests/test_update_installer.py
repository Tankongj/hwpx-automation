"""v0.16.0 auto-update foundation — 단위 + 통합 테스트.

커버리지:
- manifest 파싱 (정상/비정상/스키마 불일치)
- semver 비교 (업데이트 가용성 + patch 적용 가능성)
- SHA-256 검증 (정상/불일치/크기 불일치)
- zip 추출 + Zip Slip 방어
- Signature gate: v0.16 = None 통과 / 미래 포맷 대비 skip 로직
- helper 파일 교체 + preserved 경로 보존 + 롤백
- updater.check_for_update: manifest URL 공백 / 404 / 정상
- 전체 install_update 엔드투엔드 (로컬 HTTP 서버 기반)
"""
from __future__ import annotations

import hashlib
import http.server
import json
import shutil
import socket
import socketserver
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Iterator

import pytest

from src.commerce.update_manifest import (
    MANIFEST_SCHEMA_VERSION,
    UpdateAsset,
    UpdateManifest,
    can_apply_patch,
    choose_asset,
    is_update_available,
    parse_manifest,
    parse_semver,
)
from src.commerce.update_installer import (
    PRESERVED_PATHS,
    download_asset,
    extract_to_staging,
    install_update,
    verify_download,
    verify_signature,
)
from src.commerce.update_helper import (
    _is_preserved,
    apply_staging,
    backup_dir,
    restore_backup,
)
from src.commerce import updater as updater_mod


# --------------------------------------------------------------------------
# 1. manifest 파싱 & 버전 로직
# --------------------------------------------------------------------------

def _valid_manifest_dict(*, version="0.16.0", current="0.15.0") -> dict:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "latest": {
            "version": version,
            "released": "2026-05-01T00:00:00Z",
            "notes_url": "https://github.com/example/hwpx-automation/releases/tag/v0.16.0",
            "patch": {
                "from_version": current,
                "url": "https://example.com/patch.zip",
                "sha256": "a" * 64,
                "size_bytes": 5_000_000,
            },
            "full": {
                "url": "https://example.com/full.zip",
                "sha256": "b" * 64,
                "size_bytes": 600_000_000,
            },
            "signature": None,
            "min_supported_version": "0.10.0",
        },
    }


def test_parse_manifest_valid():
    m = parse_manifest(_valid_manifest_dict())
    assert m.ok
    assert m.version == "0.16.0"
    assert m.patch is not None and m.patch.size_bytes == 5_000_000
    assert m.full is not None and m.full.size_bytes == 600_000_000
    assert m.signature is None
    assert m.min_supported_version == "0.10.0"


def test_parse_manifest_unsupported_schema():
    bad = _valid_manifest_dict()
    bad["schema_version"] = 999
    m = parse_manifest(bad)
    assert not m.ok
    assert "schema_version" in m.error


def test_parse_manifest_missing_latest():
    m = parse_manifest({"schema_version": MANIFEST_SCHEMA_VERSION})
    assert not m.ok
    assert "latest" in m.error


def test_parse_manifest_missing_version():
    bad = _valid_manifest_dict()
    bad["latest"]["version"] = ""
    m = parse_manifest(bad)
    assert not m.ok


def test_parse_manifest_both_assets_missing():
    bad = _valid_manifest_dict()
    del bad["latest"]["patch"]
    del bad["latest"]["full"]
    m = parse_manifest(bad)
    assert not m.ok


def test_parse_manifest_rejects_bad_sha():
    bad = _valid_manifest_dict()
    bad["latest"]["patch"]["sha256"] = "not-a-sha"
    m = parse_manifest(bad)
    # patch 가 rejected 돼도 full 이 있으니 ok 로 간주. 단 patch 는 None.
    assert m.ok
    assert m.patch is None
    assert m.full is not None


def test_parse_manifest_non_dict_root():
    assert not parse_manifest("not a dict").ok
    assert not parse_manifest(None).ok
    assert not parse_manifest(42).ok


def test_parse_semver_variants():
    assert parse_semver("0.15.0") == (0, 15, 0)
    assert parse_semver("v0.15.0") == (0, 15, 0)
    assert parse_semver("0.15.0-rc1") == (0, 15, 0)
    assert parse_semver("1.0") == (1, 0, 0)


def test_is_update_available():
    m = parse_manifest(_valid_manifest_dict(version="0.16.0"))
    assert is_update_available("0.15.0", m)
    assert not is_update_available("0.16.0", m)
    assert not is_update_available("0.17.0", m)


def test_is_update_available_with_broken_manifest():
    assert not is_update_available("0.15.0", UpdateManifest(error="bad"))


def test_can_apply_patch_happy_path():
    m = parse_manifest(_valid_manifest_dict(version="0.16.0", current="0.15.0"))
    assert can_apply_patch("0.15.0", m)


def test_can_apply_patch_rejects_below_min_supported():
    m = parse_manifest(_valid_manifest_dict())  # min_supported=0.10.0
    assert not can_apply_patch("0.9.0", m)


def test_can_apply_patch_rejects_below_from_version():
    raw = _valid_manifest_dict(version="0.16.0")
    raw["latest"]["patch"]["from_version"] = "0.15.0"
    m = parse_manifest(raw)
    # 0.14.0 사용자는 이 patch 를 받을 수 없음 (full 필요)
    assert not can_apply_patch("0.14.0", m)


def test_choose_asset_prefers_patch():
    m = parse_manifest(_valid_manifest_dict(version="0.16.0"))
    a = choose_asset("0.15.0", m, prefer_patch=True)
    assert a is not None and a.url.endswith("patch.zip")


def test_choose_asset_falls_back_to_full():
    raw = _valid_manifest_dict(version="0.16.0")
    raw["latest"]["patch"]["from_version"] = "0.16.0"  # 0.15.0 은 patch 불가
    m = parse_manifest(raw)
    a = choose_asset("0.15.0", m, prefer_patch=True)
    assert a is not None and a.url.endswith("full.zip")


def test_choose_asset_force_full():
    m = parse_manifest(_valid_manifest_dict(version="0.16.0"))
    a = choose_asset("0.15.0", m, prefer_patch=False)
    assert a is not None and a.url.endswith("full.zip")


# --------------------------------------------------------------------------
# 2. 다운로드 + SHA 검증
# --------------------------------------------------------------------------

@pytest.fixture
def tmp_workdir(tmp_path: Path) -> Path:
    d = tmp_path / "workdir"
    d.mkdir()
    return d


def _make_dummy_zip(out: Path, files: dict[str, bytes]) -> tuple[Path, str, int]:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    return out, sha, out.stat().st_size


def test_verify_download_accepts_matching_sha(tmp_workdir: Path):
    z, sha, size = _make_dummy_zip(tmp_workdir / "t.zip", {"a.txt": b"hello"})
    asset = UpdateAsset(url="x", sha256=sha, size_bytes=size)
    ok, msg = verify_download(z, asset)
    assert ok, msg


def test_verify_download_rejects_wrong_sha(tmp_workdir: Path):
    z, _, size = _make_dummy_zip(tmp_workdir / "t.zip", {"a.txt": b"hello"})
    bad_asset = UpdateAsset(url="x", sha256="0" * 64, size_bytes=size)
    ok, msg = verify_download(z, bad_asset)
    assert not ok and "SHA-256" in msg


def test_verify_download_rejects_wrong_size(tmp_workdir: Path):
    z, sha, _ = _make_dummy_zip(tmp_workdir / "t.zip", {"a.txt": b"hello"})
    bad_asset = UpdateAsset(url="x", sha256=sha, size_bytes=999999)
    ok, msg = verify_download(z, bad_asset)
    assert not ok and "크기" in msg


def test_verify_download_missing_file(tmp_workdir: Path):
    asset = UpdateAsset(url="x", sha256="0" * 64)
    ok, msg = verify_download(tmp_workdir / "nope.zip", asset)
    assert not ok


# --------------------------------------------------------------------------
# 3. Signature gate — v0.16 스킵, 미래 검증 로직 stub
# --------------------------------------------------------------------------

def test_verify_signature_absent_is_ok(tmp_workdir: Path):
    z, _, _ = _make_dummy_zip(tmp_workdir / "t.zip", {"a.txt": b"x"})
    m = UpdateManifest(version="0.16.0", signature=None)
    ok, reason = verify_signature(z, m)
    assert ok
    assert "v0.16" in reason or "absent" in reason


def test_verify_signature_present_is_stub(tmp_workdir: Path):
    z, _, _ = _make_dummy_zip(tmp_workdir / "t.zip", {"a.txt": b"x"})
    m = UpdateManifest(version="0.17.0", signature="base64-sig-here")
    ok, reason = verify_signature(z, m)
    # v0.16 단계에서는 stub 로 True; v0.17 구현 후 Azure 검증 코드 연결
    assert ok
    assert "stub" in reason or "pending" in reason


# --------------------------------------------------------------------------
# 4. Zip 추출 + Zip Slip 방어
# --------------------------------------------------------------------------

def test_extract_to_staging_normal(tmp_workdir: Path):
    z, _, _ = _make_dummy_zip(
        tmp_workdir / "t.zip",
        {"a.txt": b"hello", "sub/b.txt": b"world"},
    )
    out = extract_to_staging(z, tmp_workdir / "staging")
    assert (out / "a.txt").read_bytes() == b"hello"
    assert (out / "sub" / "b.txt").read_bytes() == b"world"


def test_extract_to_staging_rejects_zip_slip(tmp_workdir: Path):
    z = tmp_workdir / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.txt", b"pwned")
    with pytest.raises(RuntimeError, match="부정 경로"):
        extract_to_staging(z, tmp_workdir / "staging")


# --------------------------------------------------------------------------
# 5. Helper: preserved paths & backup / restore
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel, expected",
    [
        ("config.json", True),
        ("user_db.sqlite", True),
        ("logs/app.log", True),
        ("logs", True),
        ("_internal/src/main.py", False),
        ("HwpxAutomation.exe", False),
    ],
)
def test_is_preserved(rel: str, expected: bool):
    assert _is_preserved(Path(rel)) is expected


def test_backup_and_restore_roundtrip(tmp_workdir: Path):
    src = tmp_workdir / "app"
    src.mkdir()
    (src / "a.txt").write_text("alpha")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("beta")

    backup = tmp_workdir / "app.bak"
    backup_dir(src, backup)
    assert (backup / "a.txt").read_text() == "alpha"
    assert (backup / "sub" / "b.txt").read_text() == "beta"

    # 원본 파괴
    shutil.rmtree(src)
    restore_backup(backup, src)
    assert (src / "a.txt").read_text() == "alpha"
    assert (src / "sub" / "b.txt").read_text() == "beta"


def test_apply_staging_preserves_config(tmp_workdir: Path):
    target = tmp_workdir / "app"
    target.mkdir()
    (target / "_internal").mkdir()
    (target / "_internal" / "old.py").write_text("old")
    (target / "config.json").write_text('{"user": "kept"}')
    (target / "user_db.sqlite").write_bytes(b"\x00IMPORTANT")

    staging = tmp_workdir / "staging"
    staging.mkdir()
    (staging / "_internal").mkdir()
    (staging / "_internal" / "new.py").write_text("new")
    # staging 에 config.json 이 있어도 preserved 경로라 덮어쓰지 않아야 함
    (staging / "config.json").write_text('{"user": "OVERWRITTEN"}')

    apply_staging(staging, target)

    assert (target / "_internal" / "old.py").exists()  # 남음 (staging 에 없으므로)
    assert (target / "_internal" / "new.py").read_text() == "new"
    assert (target / "config.json").read_text() == '{"user": "kept"}'  # preserved
    assert (target / "user_db.sqlite").read_bytes() == b"\x00IMPORTANT"  # preserved


def test_apply_staging_handles_wrapped_layout(tmp_workdir: Path):
    """staging/HwpxAutomation/ 래핑 레이아웃도 처리."""
    target = tmp_workdir / "HwpxAutomation"
    target.mkdir()
    (target / "_internal").mkdir()

    staging = tmp_workdir / "staging"
    staging.mkdir()
    wrapped = staging / "HwpxAutomation"
    wrapped.mkdir()
    (wrapped / "_internal").mkdir()
    (wrapped / "_internal" / "new.py").write_text("from wrapped")

    apply_staging(staging, target)
    assert (target / "_internal" / "new.py").read_text() == "from wrapped"


# --------------------------------------------------------------------------
# 6. updater.check_for_update — manifest URL 조건
# --------------------------------------------------------------------------

def test_check_for_update_skips_when_url_empty():
    info = updater_mod.check_for_update("0.15.0", manifest_url="")
    assert not info.available
    assert "미설정" in info.error


def test_check_for_update_with_404(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 404

        def json(self):
            return {}

    def fake_get(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)
    info = updater_mod.check_for_update(
        "0.15.0", manifest_url="https://fake.example/api/manifest.json"
    )
    assert not info.available
    assert "404" in info.error


def test_check_for_update_with_valid_manifest(monkeypatch):
    import httpx

    payload = _valid_manifest_dict(version="0.16.0", current="0.15.0")

    class FakeResp:
        status_code = 200

        def json(self):
            return payload

    def fake_get(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)
    info = updater_mod.check_for_update(
        "0.15.0",
        manifest_url="https://fake.example/api/manifest.json",
        prefer_patch=True,
    )
    assert info.available
    assert info.latest == "0.16.0"
    assert info.update_type == "patch"
    assert info.asset_url.endswith("patch.zip")
    assert info.asset_sha256 == "a" * 64


def test_check_for_update_network_error(monkeypatch):
    import httpx

    def boom(*args, **kwargs):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "get", boom)
    info = updater_mod.check_for_update("0.15.0", manifest_url="https://x")
    assert not info.available
    assert "네트워크" in info.error


# --------------------------------------------------------------------------
# 7. 엔드투엔드 download_asset (로컬 HTTP 서버)
# --------------------------------------------------------------------------

@pytest.fixture
def local_http_server(tmp_workdir: Path) -> Iterator[tuple[str, Path]]:
    """임시 디렉토리를 서빙하는 HTTP 서버. yield (base_url, serve_dir)."""
    serve_dir = tmp_workdir / "www"
    serve_dir.mkdir()

    # 빈 포트 획득
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    handler_cls = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(serve_dir), **kw
    )
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # 서버 준비 대기
        time.sleep(0.1)
        yield f"http://127.0.0.1:{port}", serve_dir
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_download_asset_roundtrip(tmp_workdir: Path, local_http_server: tuple[str, Path]):
    base_url, serve_dir = local_http_server
    zip_path, sha, size = _make_dummy_zip(serve_dir / "patch.zip", {"x.txt": b"payload"})

    asset = UpdateAsset(
        url=f"{base_url}/patch.zip", sha256=sha, size_bytes=size,
    )
    dest = tmp_workdir / "downloads"
    got = download_asset(asset, dest, timeout=10.0)
    assert got.exists()
    ok, msg = verify_download(got, asset)
    assert ok, msg


def test_install_update_end_to_end_no_relaunch(
    tmp_workdir: Path, local_http_server: tuple[str, Path], monkeypatch,
):
    """다운로드→검증→staging→(helper spawn 은 mock) 전체 흐름."""
    base_url, serve_dir = local_http_server
    app_dir = tmp_workdir / "app"
    app_dir.mkdir()
    (app_dir / "_internal").mkdir()
    (app_dir / "_internal" / "old.py").write_text("old")

    # patch zip 준비
    _, sha, size = _make_dummy_zip(
        serve_dir / "patch.zip",
        {"_internal/new.py": b"# v0.16 new"},
    )
    asset = UpdateAsset(url=f"{base_url}/patch.zip", sha256=sha, size_bytes=size)
    manifest = UpdateManifest(version="0.16.0", patch=asset, signature=None)

    # helper spawn 은 실제 실행 대신 mock
    captured = {}

    def fake_spawn(staging_dir, app_dir_, *, new_version, relaunch):
        captured["staging"] = staging_dir
        captured["target"] = app_dir_
        captured["version"] = new_version
        return 99999

    monkeypatch.setattr(
        "src.commerce.update_installer.spawn_helper", fake_spawn,
    )

    temp_root = tmp_workdir / "tmp"
    result = install_update(
        asset, manifest,
        app_dir=app_dir, new_version="0.16.0",
        temp_root=temp_root, relaunch=False,
    )
    assert result.ok, result.message
    assert result.helper_pid == 99999
    assert captured["version"] == "0.16.0"
    assert captured["target"] == app_dir
    # staging 에 zip 풀린 결과 확인
    assert (captured["staging"] / "_internal" / "new.py").read_text() == "# v0.16 new"


def test_install_update_bad_sha_aborts(tmp_workdir: Path, local_http_server: tuple[str, Path]):
    base_url, serve_dir = local_http_server
    _, real_sha, size = _make_dummy_zip(serve_dir / "patch.zip", {"a.txt": b"x"})

    # 일부러 잘못된 sha 를 가진 asset
    bad_asset = UpdateAsset(url=f"{base_url}/patch.zip", sha256="f" * 64, size_bytes=size)
    manifest = UpdateManifest(version="0.16.0", patch=bad_asset, signature=None)

    result = install_update(
        bad_asset, manifest,
        app_dir=tmp_workdir / "app", new_version="0.16.0",
        temp_root=tmp_workdir / "tmp", relaunch=False,
    )
    assert not result.ok
    assert "SHA-256" in result.message


# --------------------------------------------------------------------------
# 8. AppConfig 통합
# --------------------------------------------------------------------------

def test_appconfig_has_update_manifest_url():
    from src.settings.app_config import AppConfig

    cfg = AppConfig()
    # 플레이스홀더는 빈 문자열이 기본 — 체크 건너뛰도록 설계됨
    assert hasattr(cfg, "update_manifest_url")
    assert cfg.update_manifest_url == ""
    assert cfg.update_prefer_patch is True


def test_appconfig_preserves_legacy_repo_field():
    """v0.7~v0.15 코드가 AppConfig.update_repo 에 접근하는 걸 깨지 않도록."""
    from src.settings.app_config import AppConfig

    cfg = AppConfig()
    assert hasattr(cfg, "update_repo")
    # legacy 값이지만 존재 자체는 유지
    assert isinstance(cfg.update_repo, str)
