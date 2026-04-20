# HWPX Automation v2 — Claude Code 프로젝트 컨텍스트

> 이 파일은 새 세션마다 자동 로드됩니다. 최소한의 "변하지 않는" 핵심만 유지하세요.
> 최근 작업 이력은 `.claude/memory/index.md` 에서 자동 주입됩니다.

## 프로젝트 개요

- **목적**: AI 기반 HWPX 문서 자동화 데스크톱 앱 (한국 공무원/행정사/법무사/변호사 대상)
- **엔진**: Gemini 2.5/3.x Flash + Self-MoA + Batch API
- **스택**: Python 3.11+, PySide6, lxml, python-hwpx, FastMCP, SQLite
- **현재 버전**: `pyproject.toml` / `src/__init__.py` 를 기준으로 확인 (CHANGELOG 와 불일치 시 코드 우선)

## 응대 규칙

- **한국어로 답변** (한국 시장 타겟; 메모리/주석은 영어도 가능)
- **커밋 요청 없이는 커밋 금지** — 사용자가 명시적으로 요청할 때만
- **테스트는 항상** `pytest tests/` 로 실행 — 현재 400+ 통과 중
- **릴리스 플로우**는 `scripts/make_release.py` → `scripts/verify_release_zip.py`
- **상업용 라이선스 의무 사항**: 쿠팡 파트너스 공시문, AI 생성 공시 (AI 기본법), OSS_NOTICES

## 코드 지도 (자주 쓰는 경로)

| 경로 | 역할 |
|---|---|
| `src/parser/` | Gemini / Self-MoA / Batch / Instructor 리졸버 |
| `src/hwpx/` | HWPX 쓰기 (md_to_hwpx, hwpx_writer, hwpx_lib_adapter) |
| `src/checklist/` | HWP 텍스트 추출, G2B 크롤링 |
| `src/commerce/` | 광고 / 인증 (Firebase) / 수익 텔레메트리 / AI 공시 |
| `src/gui/` | PySide6 탭 / 위젯 / 워커 |
| `src/mcp_server/` | FastMCP in-process 서버 |
| `scripts/` | 릴리스, 인증 점검, 세션 메모리 도구 |
| `.claude/skills/` | Claude Code 스킬 (hwpx-verify, gemini-hierarchy, pro-tier-gate, release-flow) |
| `.claude/memory/` | 계층형 세션 메모리 (자동 관리) |

## 외부 절차 (코드 아닌 TODO)

- Azure Trusted Signing 가입 + `scripts/sign_release.py --run`
- TTA GS 인증 제출 (2-3개월)
- 나라장터 종합쇼핑몰 등록 (GS 인증 후)
- 쿠팡 파트너스 설정 입력: `partner_id=982081`, `tracking_code=AF7480765`

## 최근 세션 메모리

`.claude/hooks/session_start.py` 가 `/compact` 이후 또는 새 세션 시작 시 아래 인덱스를 자동 주입합니다:

@.claude/memory/index.md

상세 내역은 `.claude/memory/sessions/` 아래 개별 파일 참조.

## 참고

- 프로젝트 규칙 / 개인 메모리: `~/.claude/projects/D--03-antigravity-25-hwpx-automation-v2/memory/MEMORY.md` (사용자별)
- 세션 메모리 시스템 설계: `.claude/memory/README.md`
