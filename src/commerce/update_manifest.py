"""Update manifest 파싱 — v0.16.0.

Firebase App Hosting 이 서빙하는 ``/api/manifest.json`` 의 스키마와 검증 로직.

**스키마 (v1)**::

    {
      "schema_version": 1,
      "latest": {
        "version": "0.16.0",
        "released": "2026-05-01T00:00:00Z",
        "notes_url": "https://github.com/.../releases/tag/v0.16.0",
        "patch": {
          "from_version": "0.15.0",
          "url": "https://xxx.web.app/releases/v0.16.0/patch-from-0.15.0.zip",
          "sha256": "abc123...",
          "size_bytes": 5242880
        },
        "full": {
          "url": "https://xxx.web.app/releases/v0.16.0/full.zip",
          "sha256": "def456...",
          "size_bytes": 642428928
        },
        "signature": null,
        "min_supported_version": "0.10.0"
      }
    }

**서명 필드**: 현재 (v0.16.0) 는 항상 ``None``. Azure Trusted Signing 도입 후
(v0.17 이상 예상) 에는 Base64 인코딩된 서명 문자열이 들어가며, 설치기가 검증함.
그때까지는 SHA-256 무결성만으로 충분하다 (MitM 방지 + 손상 감지).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


MANIFEST_SCHEMA_VERSION = 1


@dataclass
class UpdateAsset:
    """patch 또는 full zip 하나."""
    url: str
    sha256: str
    size_bytes: int = 0
    from_version: str = ""  # patch 에서만 사용 ("이 버전 이상에서만 적용 가능")


@dataclass
class UpdateManifest:
    """파싱된 manifest. 실패 시 ``error`` 가 채워지고 나머지는 기본값."""
    version: str = ""
    released: str = ""
    notes_url: str = ""
    patch: Optional[UpdateAsset] = None
    full: Optional[UpdateAsset] = None
    signature: Optional[str] = None  # v0.16.0: 항상 None. Azure 도입 후 사용.
    min_supported_version: str = "0.0.0"
    schema_version: int = MANIFEST_SCHEMA_VERSION
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.version)


def parse_semver(v: str) -> tuple[int, ...]:
    """'0.15.0' / 'v0.16.0' / '0.16.0-rc1' → (0, 15, 0)."""
    v = (v or "").strip().lstrip("v")
    v = v.split("-", 1)[0]
    parts = [int(x) for x in re.findall(r"\d+", v)]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _parse_asset(raw: Any, *, is_patch: bool) -> Optional[UpdateAsset]:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url", "")).strip()
    sha = str(raw.get("sha256", "")).strip().lower()
    if not url or not sha:
        return None
    if len(sha) != 64 or not re.fullmatch(r"[0-9a-f]+", sha):
        return None
    try:
        size = int(raw.get("size_bytes", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    return UpdateAsset(
        url=url,
        sha256=sha,
        size_bytes=size,
        from_version=str(raw.get("from_version", "")).strip() if is_patch else "",
    )


def parse_manifest(data: Any) -> UpdateManifest:
    """JSON 딕셔너리 → UpdateManifest. 예외 없이 ``error`` 로 반환."""
    if not isinstance(data, dict):
        return UpdateManifest(error="manifest root must be a dict")

    schema = int(data.get("schema_version", 0) or 0)
    if schema != MANIFEST_SCHEMA_VERSION:
        return UpdateManifest(
            error=f"unsupported schema_version={schema} (expected {MANIFEST_SCHEMA_VERSION})"
        )

    latest = data.get("latest")
    if not isinstance(latest, dict):
        return UpdateManifest(error="missing 'latest' section")

    version = str(latest.get("version", "")).strip()
    if not version:
        return UpdateManifest(error="missing latest.version")

    patch = _parse_asset(latest.get("patch"), is_patch=True)
    full = _parse_asset(latest.get("full"), is_patch=False)
    if patch is None and full is None:
        return UpdateManifest(error="manifest has neither patch nor full asset")

    return UpdateManifest(
        version=version,
        released=str(latest.get("released", "")),
        notes_url=str(latest.get("notes_url", "")),
        patch=patch,
        full=full,
        signature=latest.get("signature"),  # None 그대로 유지 (v0.16 정상)
        min_supported_version=str(latest.get("min_supported_version", "0.0.0")),
        schema_version=schema,
    )


def is_update_available(current: str, manifest: UpdateManifest) -> bool:
    """현재 버전보다 manifest.version 이 더 높으면 True."""
    if not manifest.ok:
        return False
    try:
        return parse_semver(manifest.version) > parse_semver(current)
    except Exception:
        return False


def can_apply_patch(current: str, manifest: UpdateManifest) -> bool:
    """현재 버전에서 manifest.patch 로 갈 수 있는지.

    조건:
      1. manifest 에 patch 자산이 있다
      2. patch.from_version <= current (즉, 현재 버전에서 적용 가능한 patch)
      3. current >= min_supported_version
    """
    if not manifest.ok or manifest.patch is None:
        return False
    try:
        cur = parse_semver(current)
        if cur < parse_semver(manifest.min_supported_version):
            return False
        if manifest.patch.from_version:
            # 'from_version' 은 "이 버전 이상에서만 patch 가능" 을 의미
            if cur < parse_semver(manifest.patch.from_version):
                return False
        return True
    except Exception:
        return False


def choose_asset(
    current: str, manifest: UpdateManifest, *, prefer_patch: bool = True
) -> Optional[UpdateAsset]:
    """적용할 asset 하나 선택. patch 가능하면 patch, 아니면 full, 둘 다 없으면 None."""
    if not manifest.ok:
        return None
    if prefer_patch and can_apply_patch(current, manifest):
        return manifest.patch
    return manifest.full or manifest.patch


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "UpdateAsset",
    "UpdateManifest",
    "parse_semver",
    "parse_manifest",
    "is_update_available",
    "can_apply_patch",
    "choose_asset",
]
