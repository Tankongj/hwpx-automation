"""publish_firebase.py — 릴리스 asset 을 Firebase Hosting 에 업로드.

**사전 조건**:
1. Firebase CLI 설치: ``npm install -g firebase-tools``
2. 로그인: ``firebase login:ci`` → CI 토큰 받기 (또는 서비스 계정 JSON)
3. 프로젝트 생성: Firebase 콘솔에서 ``hwpx-automation`` 프로젝트 + Hosting 활성화

**CI 사용 (GitHub Actions 환경)**::

    firebase deploy --only hosting \\
        --project hwpx-automation \\
        --token "$FIREBASE_TOKEN"

**로컬 사용**::

    python scripts/publish_firebase.py \\
        --release-dir release/v0.16.0 \\
        --version 0.16.0 \\
        --project hwpx-automation

이 스크립트가 하는 일:
1. ``release-dir`` 에서 full.zip / patch-*.zip / manifest.json 검증
2. ``firebase.json`` 의 hosting public 폴더로 자산 복사 (``public/``)
3. ``firebase deploy --only hosting`` 호출
4. 성공 시 최종 URL 출력

**주의**: 실제 파일 업로드는 Firebase CLI 가 수행. 이 스크립트는 오케스트레이션만.
Firebase 프로젝트가 아직 없으면 ``--dry-run`` 으로 로컬 구성만 확인 가능.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


REPO_ROOT = Path(__file__).resolve().parents[1]
HOSTING_PUBLIC = REPO_ROOT / "firebase_hosting" / "public"
FIREBASE_JSON = REPO_ROOT / "firebase.json"
FIREBASERC = REPO_ROOT / ".firebaserc"


def ensure_firebase_config(project_id: str) -> None:
    """firebase.json 과 .firebaserc 없으면 생성."""
    if not FIREBASE_JSON.exists():
        FIREBASE_JSON.write_text(
            json.dumps({
                "hosting": {
                    "public": "firebase_hosting/public",
                    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
                    "headers": [
                        {
                            "source": "/api/manifest.json",
                            "headers": [
                                {"key": "Cache-Control", "value": "public, max-age=60"},
                                {"key": "Content-Type", "value": "application/json"},
                            ],
                        },
                        {
                            "source": "**/*.zip",
                            "headers": [
                                {"key": "Cache-Control", "value": "public, max-age=31536000, immutable"},
                            ],
                        },
                    ],
                },
            }, indent=2),
            encoding="utf-8",
        )
        print(f"✨ firebase.json 생성: {FIREBASE_JSON}")

    if not FIREBASERC.exists():
        FIREBASERC.write_text(
            json.dumps({"projects": {"default": project_id}}, indent=2),
            encoding="utf-8",
        )
        print(f"✨ .firebaserc 생성: {FIREBASERC}")


def stage_assets(release_dir: Path, version: str) -> None:
    """release_dir 의 asset 들을 HOSTING_PUBLIC 아래 적절한 경로로 복사."""
    if not release_dir.exists():
        raise SystemExit(f"❌ release-dir 없음: {release_dir}")

    manifest_src = release_dir / "manifest.json"
    if not manifest_src.exists():
        raise SystemExit(f"❌ manifest.json 없음: {manifest_src}")

    # 레이아웃:
    #   firebase_hosting/public/
    #   ├── index.html
    #   ├── api/manifest.json
    #   └── releases/v0.16.0/{full.zip, patch-from-0.15.0.zip}
    api_dir = HOSTING_PUBLIC / "api"
    rel_dir = HOSTING_PUBLIC / "releases" / f"v{version}"
    api_dir.mkdir(parents=True, exist_ok=True)
    rel_dir.mkdir(parents=True, exist_ok=True)

    # manifest
    shutil.copy2(manifest_src, api_dir / "manifest.json")
    print(f"📄 manifest → api/manifest.json")

    # zip asset 들
    for zip_path in release_dir.glob("*.zip"):
        dst = rel_dir / zip_path.name
        shutil.copy2(zip_path, dst)
        size_mb = dst.stat().st_size / 1024 / 1024
        print(f"📦 {zip_path.name} → releases/v{version}/{zip_path.name} ({size_mb:.1f} MB)")

    # 간단한 랜딩 페이지 (없으면)
    landing = HOSTING_PUBLIC / "index.html"
    if not landing.exists():
        landing.write_text(
            "<!doctype html><html lang=ko><meta charset=utf-8>"
            "<title>HWPX Automation — Updates</title>"
            "<h1>HWPX Automation</h1>"
            "<p>자동 업데이트 배포 채널입니다. 최신 릴리스는 "
            "<a href=/api/manifest.json>manifest.json</a> 에서 확인하세요.</p>",
            encoding="utf-8",
        )


def run_firebase_deploy(
    project_id: str, token: str | None, dry_run: bool,
) -> int:
    """firebase deploy 실행. token 이 있으면 비대화형 (CI)."""
    if dry_run:
        print("💡 --dry-run: 실제 업로드는 건너뜀.")
        return 0

    cmd = ["firebase", "deploy", "--only", "hosting", "--project", project_id]
    if token:
        cmd.extend(["--token", token])

    firebase = shutil.which("firebase")
    if not firebase:
        print("❌ firebase CLI 가 설치되어 있지 않습니다.", file=sys.stderr)
        print("   → npm install -g firebase-tools", file=sys.stderr)
        return 2

    print(f"▶ {' '.join(cmd[:4])} ...")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return result.returncode


def main() -> int:
    p = argparse.ArgumentParser(description="Firebase Hosting 배포")
    p.add_argument("--release-dir", type=Path, required=True, help="build_patch.py 산출물 디렉토리")
    p.add_argument("--version", required=True, help="릴리스 버전 (예: 0.16.0)")
    p.add_argument("--project", default="hwpx-automation", help="Firebase 프로젝트 ID")
    p.add_argument("--token", default=None, help="Firebase CI 토큰 (env FIREBASE_TOKEN 도 가능)")
    p.add_argument("--dry-run", action="store_true", help="실제 deploy 호출 안 함")
    args = p.parse_args()

    ensure_firebase_config(args.project)
    stage_assets(args.release_dir.resolve(), args.version)

    import os
    token = args.token or os.environ.get("FIREBASE_TOKEN")
    rc = run_firebase_deploy(args.project, token, args.dry_run)
    if rc == 0 and not args.dry_run:
        print()
        print(f"✅ 배포 완료: https://{args.project}.web.app")
        print(f"   manifest: https://{args.project}.web.app/api/manifest.json")
    return rc


if __name__ == "__main__":
    sys.exit(main())
