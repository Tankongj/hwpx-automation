"""OSS 라이선스 고지 문서 자동 생성 — v0.12.0.

GS 인증 / 공공조달 등재 필수 요건: 사용 오픈소스의 라이선스 전체 목록.

이 스크립트는 ``pip-licenses`` 를 이용해 설치된 런타임 의존성만 추려 `OSS_NOTICES.md`
생성. 실행 전 ``pip install pip-licenses`` 필요.

사용::

    pip install pip-licenses
    python scripts/generate_oss_notices.py

출력:
- ``OSS_NOTICES.md`` (repo 루트) — GS 심사 제출용
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path


for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "OSS_NOTICES.md"


# runtime 의존성 (pyproject.toml 의 dependencies)
RUNTIME_PACKAGES = [
    "lxml",
    "PySide6",
    "PySide6-Essentials",
    "PySide6-Addons",
    "google-genai",
    "openai",
    "anthropic",
    "cryptography",
    "keyring",
    "pdfplumber",
    "olefile",
    "python-hwpx",
]


HEADER = """# 오픈소스 라이선스 고지 (OSS Notices)

본 제품은 아래 오픈소스 라이브러리를 사용합니다. 각 라이브러리는 해당 라이선스 조건에
따라 포함되어 있으며, 원 소스 / 라이선스 원문은 각 프로젝트 배포 경로에서 확인할 수
있습니다.

**HWPX Automation v2** — 한글(HWPX) 문서 자동화 데스크톱 앱
생성일: {date}
생성 도구: `scripts/generate_oss_notices.py`

---

"""


def get_package_info() -> list[dict]:
    """pip-licenses 로 JSON 추출."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "piplicenses",
             "--format=json",
             "--with-license-file",
             "--with-authors",
             "--with-urls",
             "--packages", *RUNTIME_PACKAGES,
             ],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        # 모듈 경로 fallback
        result = subprocess.run(
            ["pip-licenses",
             "--format=json",
             "--with-authors",
             "--with-urls",
             "--packages", *RUNTIME_PACKAGES,
             ],
            capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"❌ pip-licenses 실행 실패: {exc.stderr[-400:]}", file=sys.stderr)
        return []

    import json
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # 일부 에디션은 line-delimited JSON
        return [
            json.loads(line) for line in result.stdout.splitlines() if line.strip()
        ]


def format_package(info: dict) -> str:
    name = info.get("Name", "?")
    version = info.get("Version", "")
    license_name = info.get("License", "Unknown")
    author = info.get("Author", "") or "—"
    url = info.get("URL", "") or info.get("Home-page", "")

    block = f"## {name} {version}\n\n"
    block += f"- **License**: {license_name}\n"
    block += f"- **Author**: {author}\n"
    if url and url != "UNKNOWN":
        block += f"- **URL**: {url}\n"
    block += "\n"
    return block


def main() -> int:
    print(f"📝 OSS 고지 문서 생성 중 → {OUT}")
    packages = get_package_info()
    if not packages:
        # pip-licenses 없으면 최소한 수동 리스트라도
        print(
            "⚠️ pip-licenses 로 정보 수집 실패 → 수동 라이선스 요약으로 진행.\n"
            "   (완전한 제출용 문서는 `pip install pip-licenses` 후 재실행)",
        )
        lines = [HEADER.format(date=date.today().isoformat())]
        for pkg in RUNTIME_PACKAGES:
            lines.append(f"## {pkg}\n\n- License: (수동 확인 필요)\n\n")
    else:
        # 이름순 정렬
        packages.sort(key=lambda p: p.get("Name", "").lower())
        lines = [HEADER.format(date=date.today().isoformat())]
        for info in packages:
            lines.append(format_package(info))

    # 하단 요약
    lines.append("---\n\n")
    lines.append(
        "## 라이선스 정책 요약\n\n"
        "- MIT / Apache-2.0 / BSD-3-Clause / LGPL-3.0 허용 — 본 제품 상용 배포 가능\n"
        "- GPL-2.0 / GPL-3.0 (Strong Copyleft) 런타임 링크는 **금지**\n"
        "- Qt (LGPL-3.0) 는 dynamic linking / 교체 가능 조건 준수\n\n"
        "## 문의\n"
        "- 라이선스 관련 문의: issues @ GitHub repo\n"
        "- 라이브러리 원본: 각 섹션의 URL 참조\n",
    )

    OUT.write_text("".join(lines), encoding="utf-8")
    print(f"✅ 작성 완료: {OUT.relative_to(ROOT)} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
