# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — HWPX Automation v2 Windows 빌드.

사용:
    pyinstaller build.spec            # --onedir (권장, 기동 빠름)
    pyinstaller build.spec --clean    # 캐시 초기화 후 빌드

결과: ``dist/HwpxAutomation/HwpxAutomation.exe``
사용자는 dist/HwpxAutomation 폴더 전체를 복사해서 쓰면 된다.
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
ROOT = Path(SPECPATH).resolve()  # PyInstaller 가 주입하는 SPECPATH 사용

# ---------------------------------------------------------------------------
# Hidden imports — PyInstaller 의 static analyser 가 놓치기 쉬운 패키지
# ---------------------------------------------------------------------------
hiddenimports: list[str] = []

# google-genai: 동적 로딩이 많아 서브모듈을 통째로 수집
hiddenimports += collect_submodules("google.genai")
hiddenimports += collect_submodules("google.ai")
hiddenimports += collect_submodules("google.api_core")
# v0.3.0: OpenAI / Anthropic SDK — 선택 백엔드로 쓰일 때 lazy import 되므로 명시 수집
hiddenimports += collect_submodules("openai")
hiddenimports += collect_submodules("anthropic")
# httpx 는 모든 HTTP 클라이언트가 공유 사용
hiddenimports += collect_submodules("httpx")
# grpc 기반 통신 — 암시적 plugin 로딩
hiddenimports += collect_submodules("grpc")
hiddenimports += collect_submodules("grpc_status")
# pydantic + 기타 SDK 의존
hiddenimports += collect_submodules("pydantic")
hiddenimports += ["tenacity"]

# keyring 백엔드는 Windows 에선 Credential Manager 사용
hiddenimports += ["keyring.backends.Windows"]

# v0.5.0~v0.7.0: 신규 서브패키지는 static analysis 가 놓칠 수 있어 명시 포함
hiddenimports += collect_submodules("src.quant")
hiddenimports += collect_submodules("src.checklist")
hiddenimports += collect_submodules("src.commerce")

# v0.8.0: pdfplumber 번들
hiddenimports += collect_submodules("pdfplumber")
try:
    datas += collect_data_files("pdfplumber")
except Exception:
    pass

# v0.9.0: olefile (HWP OLE 컨테이너)
hiddenimports += ["olefile"]

# v0.12.0: 쿠팡 파트너스 광고 렌더링용 QtWebEngine (매출 채널 #1)
hiddenimports += collect_submodules("PySide6.QtWebEngineWidgets")
hiddenimports += collect_submodules("PySide6.QtWebEngineCore")

# v0.12.0: python-hwpx 라이브러리 (HWPX pure-Python 파서/라이터, lxml 대체 경로)
try:
    hiddenimports += collect_submodules("hwpx")
except Exception:
    pass

# cryptography 는 dynamic OpenSSL 로딩 — 보통 자동 감지되지만 보험
hiddenimports += collect_submodules("cryptography")

# ---------------------------------------------------------------------------
# Data files — 번들해야 할 리소스
# ---------------------------------------------------------------------------
datas: list[tuple[str, str]] = [
    # 기본 템플릿 (첫 실행 시 %APPDATA% 로 복사됨)
    (str(ROOT / "templates" / "00_기본_10단계스타일.hwpx"), "templates"),
    (str(ROOT / "templates" / "README.md"), "templates"),
]

# google-genai 가 필요로 하는 리소스 파일들
try:
    datas += collect_data_files("google.genai")
    datas += collect_data_files("google.ai")
except Exception:
    pass

# certifi 의 cacert.pem
try:
    datas += collect_data_files("certifi")
    hiddenimports.append("certifi")
except Exception:
    pass

# grpc 리소스
try:
    datas += collect_data_files("grpc")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 테스트 툴은 런타임에 필요 없음 → 바이너리 크기 절약
        "pytest",
        "pytest_qt",
        "_pytest",
        "pyinstaller",
        "PyInstaller",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HwpxAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX 없음 — 아직 환경에 없을 수 있으니 끔
    console=False,              # GUI 앱 (콘솔 창 없음)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HwpxAutomation",
)
