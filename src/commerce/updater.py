"""자동 업데이트 체크 — v0.16.0 (Firebase manifest + GitHub Releases 이중 경로).

**변경 이력**:
- v0.7.0: GitHub Releases API 조회 (알림만, 사용자 수동 재설치)
- v0.16.0: Firebase App Hosting manifest.json 조회 + patch/full 선택
  - GitHub 경로는 legacy 호환을 위해 유지 (``repo=`` 인자)

본 모듈은 "**버전 체크**" 만 담당한다. 실제 다운로드+설치는
:mod:`src.commerce.update_installer` 로 위임.

**경로 선택** (``check_for_update`` 호출 시):

1. ``manifest_url`` 주어지면 Firebase 경로 (v0.16+)
2. ``repo`` 주어지면 GitHub Releases API 경로 (v0.7~v0.15 legacy)
3. 둘 다 없으면 skip

우선순위: manifest_url > repo.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from ..utils.logger import get_logger
from .update_manifest import (
    UpdateAsset,
    UpdateManifest,
    can_apply_patch,
    choose_asset,
    is_update_available,
    parse_manifest,
)
from .update_manifest import parse_semver as _public_parse_semver


_log = get_logger("commerce.updater")


# v0.7~v0.15 호환 — 일부 테스트 / 코드가 이 private helper 를 직접 import
def _parse_semver(v: str) -> tuple[int, ...]:
    """'0.7.0' / 'v0.7.0' / '0.1.0-dev' → (N, N, N)."""
    return _public_parse_semver(v)


# GitHub Releases API (legacy)
_GITHUB_RELEASES_URL = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass
class UpdateInfo:
    """UI 레이어용 요약. 상세 manifest 는 별도로 보관.

    **필드 확장 (v0.16.0)**: ``notes_url``, ``asset_sha256``, ``asset_size_bytes``,
    ``update_type``, ``manifest`` 추가. 기존 ``release_url``, ``asset_url``, ``notes``
    는 그대로 유지 → v0.7~v0.15 호출자 호환.
    """
    available: bool
    current: str
    latest: str = ""
    release_url: str = ""
    asset_url: str = ""
    notes: str = ""
    # v0.16.0
    notes_url: str = ""
    asset_sha256: str = ""
    asset_size_bytes: int = 0
    update_type: str = ""      # "patch" / "full" (v0.16+ 만)
    manifest: Optional[UpdateManifest] = None
    error: str = ""


# --- v0.16 경로: Firebase manifest ----------------------------------------

def _check_via_manifest(
    current_version: str, manifest_url: str, *, prefer_patch: bool, timeout: float,
) -> UpdateInfo:
    try:
        resp = httpx.get(
            manifest_url.strip(),
            timeout=timeout,
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"네트워크 오류: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return UpdateInfo(
            available=False, current=current_version,
            error=f"요청 실패: {exc}",
        )

    if resp.status_code == 404:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"manifest 없음 (404): {manifest_url}",
        )
    if resp.status_code >= 400:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"HTTP {resp.status_code}",
        )

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"JSON 파싱 실패: {exc}",
        )

    manifest = parse_manifest(data)
    if not manifest.ok:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"manifest 검증 실패: {manifest.error}",
            manifest=manifest,
        )

    available = is_update_available(current_version, manifest)
    asset = choose_asset(current_version, manifest, prefer_patch=prefer_patch) if available else None
    update_type = ""
    if asset is not None:
        if manifest.patch is asset and can_apply_patch(current_version, manifest):
            update_type = "patch"
        else:
            update_type = "full"

    return UpdateInfo(
        available=available,
        current=current_version,
        latest=manifest.version,
        release_url=manifest.notes_url,
        notes_url=manifest.notes_url,
        asset_url=asset.url if asset else "",
        asset_sha256=asset.sha256 if asset else "",
        asset_size_bytes=asset.size_bytes if asset else 0,
        update_type=update_type,
        manifest=manifest,
    )


# --- v0.7~v0.15 경로: GitHub Releases API (legacy) -------------------------

def _check_via_github(
    current_version: str, repo: str, *, timeout: float,
) -> UpdateInfo:
    url = _GITHUB_RELEASES_URL.format(repo=repo)
    try:
        resp = httpx.get(
            url, timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"네트워크 오류: {exc}",
        )

    if resp.status_code == 404:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"저장소를 찾을 수 없음: {repo}",
        )
    if resp.status_code >= 400:
        return UpdateInfo(
            available=False, current=current_version,
            error=f"HTTP {resp.status_code}",
        )

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return UpdateInfo(
            available=False, current=current_version,
            error=f"JSON 파싱 실패: {exc}",
        )

    latest_tag = str(data.get("tag_name", "")).strip() or str(data.get("name", "")).strip()
    if not latest_tag:
        return UpdateInfo(
            available=False, current=current_version,
            error="최신 릴리즈 태그를 찾을 수 없음",
        )

    try:
        current_tuple = _parse_semver(current_version)
        latest_tuple = _parse_semver(latest_tag)
    except ValueError:
        return UpdateInfo(
            available=False, current=current_version,
            latest=latest_tag, error="버전 파싱 실패",
        )

    asset_url = ""
    for asset in data.get("assets", []) or []:
        if str(asset.get("name", "")).lower().endswith(".zip"):
            asset_url = str(asset.get("browser_download_url", ""))
            break

    return UpdateInfo(
        available=latest_tuple > current_tuple,
        current=current_version,
        latest=latest_tag,
        release_url=str(data.get("html_url", "")),
        asset_url=asset_url,
        notes=str(data.get("body", ""))[:1000],
    )


# --- Public API -----------------------------------------------------------

def check_for_update(
    current_version: str,
    *,
    manifest_url: str = "",
    repo: str = "",
    prefer_patch: bool = True,
    timeout: float = 5.0,
) -> UpdateInfo:
    """버전 체크. 실패해도 예외 대신 UpdateInfo 로 반환.

    Parameters
    ----------
    current_version
        현재 설치된 앱 버전 (예: ``"0.16.0"``).
    manifest_url
        Firebase Hosting 의 manifest.json URL. v0.16+ 기본 경로.
    repo
        GitHub ``{owner}/{repo}`` — manifest_url 이 없을 때 사용 (legacy).
    prefer_patch
        manifest 경로에서만 의미. True 면 patch 우선.
    timeout
        HTTP 타임아웃 (초).
    """
    if manifest_url.strip():
        return _check_via_manifest(
            current_version, manifest_url,
            prefer_patch=prefer_patch, timeout=timeout,
        )
    if repo.strip():
        return _check_via_github(current_version, repo, timeout=timeout)
    return UpdateInfo(
        available=False, current=current_version,
        error="manifest URL / repo 모두 미설정 (개발 중 / Firebase 설정 전)",
    )


# v0.7~v0.15 호환 shim
DEFAULT_REPO = "hwpx-automation"


__all__ = [
    "UpdateInfo", "UpdateAsset",
    "check_for_update",
    "DEFAULT_REPO",
    "_parse_semver",  # legacy
]
