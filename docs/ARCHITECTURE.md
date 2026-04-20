# HWPX Automation v2 — Architecture

v0.1.0 기준 내부 구조 지도. 새로 합류하는 개발자가 1 시간 안에 방향 잡을 수 있게.

## 1. 데이터 플로우 (최상위)

```
사용자 입력 (.txt 원고)
       │
       ▼
┌────────────────────────────┐
│  regex_parser              │  결정론: 90%+ 정확
│   ↳ IR Block 리스트         │
└──────────┬─────────────────┘
           │ 일부 ambiguous=True
           ▼
┌────────────────────────────┐
│  gemini_resolver (선택)     │  Gemini 2.5 Flash
│   ↳ 애매 블록 재분류         │  호출 1회/문서, ~₩10
└──────────┬─────────────────┘
           │
           ▼
┌────────────────────────────┐
│  template_analyzer          │  HWPX → StyleMap
│   ↳ 레벨별 para/char/style  │
└──────────┬─────────────────┘
           │
           ▼
┌────────────────────────────┐
│  md_to_hwpx.convert        │  v1 엔진 (XML 조작)
│   ↳ 출력 HWPX               │
└──────────┬─────────────────┘
           │
           ▼
┌────────────────────────────┐
│  fix_namespaces             │  lxml ns0:/ns1: 제거
│   ↳ 한/글 호환 보정          │
└──────────┬─────────────────┘
           │
           ▼
┌────────────────────────────┐
│  verify_hwpx                │  구조/스타일 검증
│   ↳ VerifyReport            │
└──────────┬─────────────────┘
           │
           ▼
     완성된 HWPX
```

## 2. 모듈 맵

### 엔진 계층 (`src/hwpx/`)
v1 프로젝트(Tankongj/hwpx-proposal-automation) 의 XML 조작 로직을 포팅 + 함수형 API 추가.

| 모듈 | 공개 API | 역할 |
|---|---|---|
| `fix_namespaces.py` | `fix_hwpx(path)` | lxml 직렬화 후 `ns0:/ns1:` prefix 제거, XML entity 복구, 표 페이지 넘김 보정 |
| `md_to_hwpx.py` | `convert(blocks, template, output, style_map)` | IR → HWPX. Style Remapper, Font Remapper, 페이지 넘김, secPr 보존 등 53+ iteration 검증 로직 |
| `verify_hwpx.py` | `verify(path) → VerifyReport` | 공통/advanced/정성/정량 체크. 7/11 이상 통과면 OK |
| `visualize.py` | `render_hwpx_to_html(path) → str` | header 기반 CSS 매핑 HTML. QTextBrowser 용 |

### 파서 계층 (`src/parser/`)

| 모듈 | 공개 API | 역할 |
|---|---|---|
| `ir_schema.py` | `Block` dataclass, `blocks_to_v1_paragraphs()` | IR. `level=-1` 표지 / `0` 본문 / `1~10` 계층 |
| `regex_parser.py` | `parse(text)` / `parse_file(path)` | 기획안 4.1 RULES 표. 결정론 + ambiguous 마킹 3종 |
| `gemini_resolver.py` | `resolve(blocks) → ResolveReport` | 애매 블록 배치 호출. pluggable `GeminiClient` protocol. Structured output schema (types.Schema) + thinking_budget=0 |

### 템플릿 계층 (`src/template/`)

| 모듈 | 공개 API | 역할 |
|---|---|---|
| `default_10_levels.py` | `DEFAULT_STYLE_MAP`, `V1_TYPE_STYLE_MAP` | 기획안 4.4 하드코딩 스펙 |
| `template_manager.py` | `TemplateManager` | `%APPDATA%/HwpxAutomation/templates/` CRUD + `index.json` |
| `template_analyzer.py` | `analyze(path) → StyleMap` | name heuristic → size heuristic → fallback 3단 |

### GUI 계층 (`src/gui/`)

| 모듈 | 역할 |
|---|---|
| `main_window.py` | 4탭 컨테이너 + 메뉴 + 첫 실행 온보딩 + 시그널 라우팅 |
| `error_handler.py` | `sys.excepthook` → QMessageBox (traceback 복사 버튼) |
| `tabs/convert_tab.py` | 변환 UI + 진행 로그 + 저장/미리보기 버튼 |
| `tabs/template_tab.py` | 리스트 CRUD + 우측 상세 (analyzer 결과) |
| `tabs/preview_tab.py` | QTextBrowser HTML 렌더 + 한/글로 열기 |
| `tabs/settings_tab.py` | AppConfig 편집 + API Key 관리 + 유틸 버튼 |
| `widgets/ad_placeholder.py` | MVP 숨김 / 상업화 단계에 QWebEngineView 로 교체 |
| `widgets/api_key_dialog.py` | 첫 실행 + 교체용 QDialog |
| `workers/conversion_worker.py` | QThread 파이프라인 실행. progress/step/finished/failed 시그널 |

### 설정 계층 (`src/settings/`)

| 모듈 | 역할 |
|---|---|
| `app_config.py` | `%APPDATA%/HwpxAutomation/config.json` JSON (use_gemini, model, threshold, output_dir, log_level 등) |
| `api_key_manager.py` | Windows keyring 우선, Fernet 파일 fallback. ENV 변수 override. `_delete_keyring` 대칭 + 테스트 격리 |

### 유틸 (`src/utils/`)

| 모듈 | 역할 |
|---|---|
| `logger.py` | Python logging 설정. Windows cp949 스트림을 UTF-8 으로 wrap |

## 3. 엔트리 포인트

- **`src/main.py`** — GUI 앱. 개발 시: `python -m src.main`
- **`src/cli.py`** — CLI. 개발 시: `python -m src.cli build/resolve/fix/verify/convert`
- **`launcher.py`** (프로젝트 루트) — PyInstaller 전용 래퍼. 내부에서 `src.main.main()` 호출

## 4. 확장 포인트

### 새 LLM 백엔드 추가
`src/parser/gemini_resolver.py` 의 `GeminiClient` Protocol 구현체만 추가:

```python
class OllamaClient:
    def generate(self, prompt: str) -> GenerateResult:
        ...
```

그리고 `resolve(blocks, client=OllamaClient())` 로 주입.

### 새 탭 추가
1. `src/gui/tabs/new_tab.py` 에 `QWidget` 서브클래스 생성
2. `main_window.py._build_central()` 에 `self.tabs.addTab(NewTab(), "새 탭")` 추가
3. 필요하면 메뉴 단축키(`Ctrl+5`) 연결
4. 탭 간 통신이 필요하면 시그널로 (직접 참조 금지)

### 새 검증 체크
`src/hwpx/verify_hwpx.py` 의 `_COMMON_CHECKS` / `_QUALITATIVE_CHECKS` 리스트에 `(이름, 카테고리, 함수)` 튜플 추가. 함수 시그니처: `(data, **kwargs) → (bool, str)`.

### 상업화 기능 통합
- **광고**: `src/gui/widgets/ad_placeholder.py` 의 `AdPlaceholder.activate()` 메서드를 호출 + QWebEngineView 로 교체
- **로그인**: `src/main.py` 의 `main()` 안에서 `run()` 전에 `LoginDialog` 실행
- **텔레메트리**: `src/utils/telemetry.py` 신규 모듈 + AppConfig 의 `telemetry_optin` 플래그

## 5. 테스트 구조 (`tests/`)

| 파일 | 대상 | 건수 |
|---|---|---|
| `test_w1_smoke.py` | 엔진 import, IR round-trip, E2E | 4 |
| `test_w2_parser.py` | regex_parser rules, ambiguity, fixture | 26 |
| `test_w2_pipeline.py` | parser → convert → verify E2E | 3 |
| `test_w2_template.py` | default_10_levels, manager, analyzer | 16 |
| `test_w3_api_key.py` | keyring/Fernet, ENV override, 격리 | 6 |
| `test_w3_config.py` | AppConfig load/save, unknown fields | 5 |
| `test_w3_gemini_resolver.py` | prompt build, parse, mock client, schema | 18 |
| `test_w4_gui.py` | 탭/워커/메인윈도우 pytest-qt | 10 |
| **총계** | | **~85** |

외부 픽스처:
- `tests/fixtures/2026_귀농귀촌아카데미_원고.txt` — 1680 줄 실제 제안서 (길이 중립 네이밍)
- `tests/fixtures/exe_smoke_test.py` — 빌드된 .exe 실행 검증
- `tests/fixtures/exe_e2e_test.py` — 설치 경험 E2E

## 6. 배포 (PyInstaller)

- **엔트리**: `launcher.py` (relative import 이슈 우회)
- **spec**: `build.spec` — google.genai/cryptography submodule 수집, templates 번들
- **출력**: `dist/HwpxAutomation/` (19MB exe + 210MB 라이브러리)
- **배포**: 폴더 전체 복사 → 다른 Windows PC 에서 실행

## 7. 디렉토리 레이아웃 (프로덕션 기준)

```
%APPDATA%/HwpxAutomation/
├── config.json              ← AppConfig (JSON)
├── keys.enc                 ← Fernet fallback (keyring 실패 시만)
├── logs/
│   └── hwpx-automation.log  ← 로깅 파일
└── templates/
    ├── index.json           ← 템플릿 메타데이터
    └── *.hwpx               ← 실제 템플릿 파일들
```

Windows 자격 증명 관리자(제어판 → 사용자 계정 → 자격 증명 관리자 → Windows 자격 증명):
```
인터넷 또는 네트워크 주소: HwpxAutomation
사용자 이름: gemini-api-key
암호: ●●●●●●●  (Gemini API Key)
```
