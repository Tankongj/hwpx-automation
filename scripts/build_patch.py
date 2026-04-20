"""build_patch.py — 두 dist/ 폴더를 비교해 patch zip 과 SHA-256 메타데이터 생성.

**v0.16.0 부터 사용**. PyInstaller 로 빌드한 ``dist/HwpxAutomation/`` 두 세트
(이전 버전 + 현재 버전) 가 있어야 한다.

**워크플로**::

    # 1. 이전 버전 dist 확보 (GitHub Release 에서 다운로드 or 로컬 빌드 캐시)
    #    예: release/HwpxAutomation-v0.15.0/dist/HwpxAutomation/

    # 2. 현재 버전 빌드
    pyinstaller build.spec --noconfirm
    #    → dist/HwpxAutomation/

    # 3. patch + full 자산 + manifest.json 생성
    python scripts/build_patch.py \\
        --old release/HwpxAutomation-v0.15.0/dist/HwpxAutomation \\
        --new dist/HwpxAutomation \\
        --old-version 0.15.0 \\
        --new-version 0.16.0 \\
        --output release/v0.16.0/

    # 4. release/v0.16.0/ 에 다음이 생성된다:
    #    - patch-from-0.15.0.zip  (변경된 파일만, 보통 5~20 MB)
    #    - full.zip               (전체, ~600 MB)
    #    - manifest.json          (Firebase 에 업로드할 메타)

**diff 규칙**:
- 이전에 없었고 지금 있는 파일 → added
- 양쪽 다 있지만 내용 다름 (SHA-256 비교) → modified
- 이전에 있었지만 지금 없는 파일 → removed

patch zip 에는 added + modified 파일만 포함. removed 는 zip 내 ``_patch_manifest.json``
에 파일 경로 리스트로 기록 → install_update 가 읽어서 처리 (v0.17+ 지원 예정).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# cp949 회피
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


@dataclass
class DistDiff:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def changed_count(self) -> int:
        return len(self.added) + len(self.modified) + len(self.removed)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def iter_relative_files(root: Path) -> list[Path]:
    """root 기준 상대경로로 모든 파일 (디렉토리 제외)."""
    if not root.exists():
        return []
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())


def diff_dists(old: Path, new: Path) -> DistDiff:
    """두 dist 의 파일 레벨 diff."""
    old_files = {p: sha256_file(old / p) for p in iter_relative_files(old)}
    new_files = {p: sha256_file(new / p) for p in iter_relative_files(new)}

    diff = DistDiff()
    for rel, new_sha in new_files.items():
        if rel not in old_files:
            diff.added.append(rel.as_posix())
        elif old_files[rel] != new_sha:
            diff.modified.append(rel.as_posix())
        else:
            diff.unchanged.append(rel.as_posix())
    for rel in old_files:
        if rel not in new_files:
            diff.removed.append(rel.as_posix())

    return diff


def build_patch_zip(
    new: Path, diff: DistDiff, out_zip: Path, *, new_version: str, old_version: str,
) -> tuple[str, int]:
    """변경된 파일만 담은 zip 생성. (sha256, size_bytes) 반환."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "from_version": old_version,
        "to_version": new_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "added": diff.added,
        "modified": diff.modified,
        "removed": diff.removed,
    }
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_str in diff.added + diff.modified:
            rel = Path(rel_str)
            zf.write(new / rel, arcname=rel.as_posix())
        zf.writestr("_patch_manifest.json", json.dumps(meta, ensure_ascii=False, indent=2))

    sha = sha256_file(out_zip)
    size = out_zip.stat().st_size
    return sha, size


def build_full_zip(new: Path, out_zip: Path) -> tuple[str, int]:
    """현재 dist 전체를 zip. (sha256, size_bytes) 반환."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in iter_relative_files(new):
            zf.write(new / rel, arcname=rel.as_posix())
    return sha256_file(out_zip), out_zip.stat().st_size


def build_manifest(
    *,
    new_version: str,
    old_version: str,
    patch_info: tuple[str, str, int] | None,  # (url, sha, size) or None
    full_info: tuple[str, str, int],          # (url, sha, size)
    min_supported: str = "0.10.0",
    notes_url: str = "",
    signature: str | None = None,  # v0.16 = None. v0.17+ = Azure 서명
) -> dict:
    latest: dict = {
        "version": new_version,
        "released": datetime.now(timezone.utc).isoformat(),
        "notes_url": notes_url,
        "signature": signature,
        "min_supported_version": min_supported,
        "full": {
            "url": full_info[0],
            "sha256": full_info[1],
            "size_bytes": full_info[2],
        },
    }
    if patch_info:
        latest["patch"] = {
            "from_version": old_version,
            "url": patch_info[0],
            "sha256": patch_info[1],
            "size_bytes": patch_info[2],
        }
    return {"schema_version": 1, "latest": latest}


def main() -> int:
    p = argparse.ArgumentParser(description="HWPX Automation patch builder")
    p.add_argument("--old", type=Path, help="이전 버전 dist 폴더")
    p.add_argument("--new", type=Path, required=True, help="신규 버전 dist 폴더")
    p.add_argument("--old-version", default="", help="이전 버전 번호 (patch 필요 시)")
    p.add_argument("--new-version", required=True, help="신규 버전 번호")
    p.add_argument("--output", type=Path, required=True, help="산출물 디렉토리")
    p.add_argument(
        "--base-url", default="https://hwpx-automation.web.app/releases",
        help="manifest 에 기록할 asset URL prefix",
    )
    p.add_argument(
        "--notes-url", default="",
        help="릴리스 노트 URL (GitHub Release 주소 등)",
    )
    p.add_argument(
        "--min-supported", default="0.10.0",
        help="이 버전 미만은 full 강제 (patch 미지원)",
    )
    p.add_argument("--skip-patch", action="store_true", help="patch 생성 건너뛰기 (full 만)")
    args = p.parse_args()

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    new_dir = args.new.resolve()
    if not new_dir.exists():
        print(f"❌ --new 경로 없음: {new_dir}", file=sys.stderr)
        return 2

    # 1. full zip (항상 만듦)
    full_zip = out / "full.zip"
    print(f"📦 full zip 생성: {full_zip}")
    full_sha, full_size = build_full_zip(new_dir, full_zip)
    print(f"   sha256={full_sha[:16]}... size={full_size / 1024 / 1024:.1f} MB")

    full_url = f"{args.base_url.rstrip('/')}/v{args.new_version}/full.zip"

    # 2. patch zip (이전 dist 가 있고 --skip-patch 아니면)
    patch_info: tuple[str, str, int] | None = None
    if args.old and not args.skip_patch:
        old_dir = args.old.resolve()
        if not old_dir.exists():
            print(f"⚠ --old 경로 없음, patch 스킵: {old_dir}", file=sys.stderr)
        elif not args.old_version:
            print(f"⚠ --old-version 누락, patch 스킵", file=sys.stderr)
        else:
            print(f"🔍 diff: {old_dir} → {new_dir}")
            diff = diff_dists(old_dir, new_dir)
            print(f"   added={len(diff.added)} modified={len(diff.modified)} "
                  f"removed={len(diff.removed)} unchanged={len(diff.unchanged)}")

            if diff.changed_count == 0:
                print("   (변경 없음 — patch 생성 불필요)")
            else:
                patch_zip = out / f"patch-from-{args.old_version}.zip"
                print(f"📦 patch zip 생성: {patch_zip}")
                patch_sha, patch_size = build_patch_zip(
                    new_dir, diff, patch_zip,
                    new_version=args.new_version,
                    old_version=args.old_version,
                )
                print(f"   sha256={patch_sha[:16]}... size={patch_size / 1024 / 1024:.2f} MB")
                print(f"   절약: {(full_size - patch_size) / 1024 / 1024:.1f} MB "
                      f"({(1 - patch_size / full_size) * 100:.1f}% 작음)")

                patch_url = (
                    f"{args.base_url.rstrip('/')}/v{args.new_version}/"
                    f"patch-from-{args.old_version}.zip"
                )
                patch_info = (patch_url, patch_sha, patch_size)

    # 3. manifest.json
    manifest = build_manifest(
        new_version=args.new_version,
        old_version=args.old_version or "",
        patch_info=patch_info,
        full_info=(full_url, full_sha, full_size),
        min_supported=args.min_supported,
        notes_url=args.notes_url,
        signature=None,  # v0.16 = 서명 없음
    )
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"📄 manifest.json 저장: {manifest_path}")
    print()
    print("✅ 완료. Firebase 에 업로드할 파일:")
    print(f"   {manifest_path}")
    print(f"   {full_zip}")
    if patch_info:
        print(f"   {out / f'patch-from-{args.old_version}.zip'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
