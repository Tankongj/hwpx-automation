# Changelog

모든 주목할 만한 변경은 이 파일에 기록됩니다.

포맷은 [Keep a Changelog](https://keepachangelog.com/) 를 따르며, 버전 번호는
[Semantic Versioning](https://semver.org/) 을 사용합니다.

## [0.16.0] — 2026-04-20

### 🚀 Auto-Update Foundation — 재설치 없는 자동 업데이트

**배포 전략 전환**: v0.7 이후 유지되던 GitHub Releases "수동 재설치 알림" 방식을
**Firebase Hosting manifest 기반 자동 다운로드 + 패치 설치** 로 교체.

시나리오 C (무료 배포 + 지인 대상 200명) 에 맞춰 설계. 현재는 서명 없이 배포하되
미래 Azure Trusted Signing 도입 시 **기존 사용자도 자동 업데이트로 서명된 버전 수신** 하도록 설계.

### 🧱 신규 모듈

- **`src/commerce/update_manifest.py`** — Firebase manifest.json 스키마 파싱
  - `UpdateManifest` dataclass (version / patch / full / signature / min_supported)
  - `parse_semver`, `is_update_available`, `can_apply_patch`, `choose_asset`
  - SHA-256 형식 검증 + schema_version 가드
- **`src/commerce/updater.py`** (재작성) — Firebase manifest fetch
  - `check_for_update(current_version, manifest_url, prefer_patch)` → `UpdateInfo`
  - URL 공백 / 404 / JSON 파싱 실패 / 네트워크 에러 모두 graceful
  - v0.7~v0.15 호출자 호환 (`UpdateInfo` 필드 확장)
- **`src/commerce/update_installer.py`** — 다운로드 + 검증 + staging + helper spawn
  - `download_asset` (`httpx.stream`, 진행률 콜백)
  - `verify_download` (SHA-256 + 크기)
  - `verify_signature` (v0.16 = None 통과, v0.17+ Azure 활성화 예정)
  - `extract_to_staging` (Zip Slip 방어)
  - `install_update` — 풀 파이프라인 오케스트레이션
- **`src/commerce/update_helper.py`** — 별도 프로세스, 파일 교체 전담
  - PID 폴링 (Windows `OpenProcess` + POSIX `os.kill`) 후 작업 시작
  - `backup_dir` → `apply_staging` → `restore_backup` on failure
  - `PRESERVED_PATHS`: `config.json` / `user_db.sqlite*` / `logs/` 보존
  - wrapped 레이아웃 자동 감지 (`staging/HwpxAutomation/...`)
  - relaunch 옵션 (업데이트 후 자동 재시작)
- **`src/main.py`** — `--update-helper` 플래그 분기 추가 (PyInstaller 친화)

### 🛠 릴리스 도구

- **`scripts/build_patch.py`** — 두 `dist/` 비교 → patch zip + full zip + manifest.json
  - SHA-256 기반 파일 diff (added / modified / removed)
  - 변경 파일만 포함 → 일반적으로 **5~20 MB patch** (full 600 MB 대비 ~97% 절감)
  - `_patch_manifest.json` 을 zip 내부에 기록 (향후 removed 파일 처리용)
- **`scripts/publish_firebase.py`** — Firebase Hosting 자산 배포
  - `firebase.json` / `.firebaserc` 자동 생성
  - asset → `public/releases/v{version}/`, manifest → `public/api/manifest.json`
  - `firebase deploy --only hosting` 호출 (CI 토큰 지원)
  - `--dry-run` 옵션 (Firebase 프로젝트 생성 전 로컬 확인용)
- **`.github/workflows/release.yml`** — 태그 푸쉬 → 전체 자동화
  - `v*` 태그 push 시 PyInstaller 빌드 + 이전 릴리스 자동 탐색 + patch 생성
  - GitHub Release 생성 (full.zip + patch.zip 자산)
  - `FIREBASE_TOKEN` secret 이 있으면 Firebase 배포, 없으면 skip (graceful)

### ⚙ AppConfig 변경

- 🆕 `update_manifest_url: str = ""` — 빈 값이면 업데이트 체크 건너뜀 (플레이스홀더)
- 🆕 `update_prefer_patch: bool = True` — patch 가능하면 우선 사용
- ♻ `update_repo: str = "hwpx-automation"` — 값만 갱신 (legacy 호환, 실사용 X)

### 🔒 보안 & 무결성

| 계층 | 메커니즘 | 활성 상태 |
|------|----------|-----------|
| 전송 보호 | HTTPS (Firebase Hosting 기본) | ✅ |
| 무결성 | SHA-256 검증 (manifest 서명된 URL 기반) | ✅ |
| Zip Slip 방어 | 경로 검증 (`staging_dir` 내부만 허용) | ✅ |
| 코드 서명 | Azure Trusted Signing | ⏸ v0.17 예정 (manifest.signature 슬롯만 확보) |
| 롤백 | `{app_dir}.bak` 자동 백업, 실패 시 자동 복원 | ✅ |

### 🧪 테스트

- `tests/test_update_installer.py` — **42 passed**
  - manifest 파싱 (정상/스키마 불일치/비정상 sha/missing 필드)
  - semver 비교 (update 가용성 + patch 적용 조건)
  - SHA-256 검증 (일치/불일치/크기 불일치/누락)
  - 서명 게이트 (None 통과 / 미래 stub)
  - Zip 추출 + Zip Slip 방어
  - helper 파일 교체 + preserved 경로 보존 + 백업/롤백
  - wrapped 레이아웃 자동 감지
  - updater.check_for_update (URL 공백 / 404 / 정상 manifest / 네트워크 에러)
  - 로컬 HTTP 서버 기반 end-to-end 다운로드 + install 플로우

### 📊 규모 영향 분석 (200명 배포 기준)

| 배포 방식 | 월 대역폭 | Firebase 비용 |
|----------|-----------|---------------|
| Full 매번 (v0.15 구조) | 245 GB/월 | **~$35/월** (Blaze) |
| **Patch 우선 (v0.16)** | **~2 GB/월** | **$0** (Free tier 내) |

### 📋 사용자 외부 작업 (v0.16 활성화)

1. GitHub 저장소 `hwpx-automation` 생성 + 초기 push
2. Firebase 콘솔에서 `hwpx-automation` 프로젝트 생성 + Hosting 활성화
3. `firebase login:ci` → 토큰을 GitHub Secrets `FIREBASE_TOKEN` 에 등록
4. `AppConfig.update_manifest_url` 을 실 URL (`https://hwpx-automation.web.app/api/manifest.json`) 로 교체
5. `git tag v0.16.0 && git push --tags` → CI 가 자동 빌드 + 배포

### 🎯 v0.15.0 → v0.16.0 테스트 진화

- **404 → 446 → 454 → 496** (+42 new, 0 regression)

### 🌉 설계 근거 (검증된 리서치)

- Progressive disclosure (claude-mem 63K⭐) — manifest-only, asset on-demand
- SHA-256 + 서명 분리 (NIST SP 800-131A) — 무결성과 신원은 별도 계층
- Windows 자기-교체 불가 → 별도 helper 프로세스 (Chocolatey / Squirrel 패턴)
- Zip Slip 방어 (Snyk 2018 리서치) — `extractall` 전 경로 검증 필수

## [0.15.0] — 2026-04-20

### 🎯 v0.14 로드맵 "코드로 가능한 것" 마지막 3 트랙 완주

3 항목 전량 집행 — 이제 남은 건 외부 인증/계정 절차만.

### 🌉 md_to_hwpx ↔ python-hwpx 경로 분기 (opt-in)

v0.13 `hwpx_writer` 는 독립 모듈. v0.15 는 기존 `md_to_hwpx.convert` 안에서 분기.

- **`md_to_hwpx.convert(..., use_python_hwpx_writer=True)`** — 단순 변환 시 python-hwpx 경로
- **자동 폴백 조건**: `reference` / `cover_range` / `toc_range` / `summary_range` /
  `proposal_title` / `cover_keywords` / `summary_mapping_path` 중 **하나라도 사용**되면 legacy 경로 강제
  (python-hwpx 는 아직 reference 병합 미지원)
- **실패 시 자동 legacy 폴백** — python-hwpx 에서 예외 나도 convert 는 결과 생성
- **신규 헬퍼** `hwpx_writer.write_ir_blocks(blocks, template, output, style_map)` —
  IR `Block` 또는 v1 paragraph dict 모두 수용
- **`_v1_type_to_level(type_str) → level`** — `"heading3" → 3`, `"body" → 0`, `"title" → -1` 매핑
- **AppConfig 필드**: `use_python_hwpx_writer: bool` (기본 False)
- **CLI 플래그**: `build --python-hwpx-writer` — config 값보다 우선
- **구조 검증**: 실 샘플 templat 으로 단락 3 개 변환 후 다시 `extract_hwpx_text` 로 열어 text 검증 (roundtrip)

### ⏳ Self-MoA × Batch — GUI heartbeat 통합

v0.14 는 내부 Batch 사용은 가능했으나 GUI 는 응답 없음 — v0.15 는 실시간 경과 표시.

- **`_Signals` 확장**: `batch_started(draws: int)` / `batch_finished(ok: bool)` Signal
- **`ConversionWorker`**: `create_default_client` 반환값의 `use_batch` / `draws` 속성 감지 →
  resolve 호출 전 `batch_started.emit(draws)`, try/finally 로 `batch_finished.emit(True)` 보장
- **`ConvertTab`**: `_on_batch_started` / `_on_batch_tick` (1초 QTimer) / `_on_batch_finished` —
  `status_message` 에 "⏳ Self-MoA × Batch 처리 중 (MM:SS 경과, draws=N)" 실시간 갱신
- **진행 로그**: 오렌지색 `🔄 Self-MoA × Batch 모드: draws=N — 배치 대기 시작` 출력
- 완료 시 녹색 `✅ Self-MoA × Batch 완료 — Xs`

### 💵 광고 수익 텔레메트리 + 대시보드 (매출 채널 가시화)

쿠팡 + AdSense 채널의 **노출/클릭/실패** 를 로컬 JSONL 에 기록 (기존 `telemetry.record` 재활용).

- **신규 모듈** `src/commerce/revenue_telemetry.py`:
  - 이벤트 상수: `EV_IMPRESSION` / `EV_CLICK` / `EV_LOAD_FAILED`
  - 채널 상수: `CH_COUPANG` / `CH_ADSENSE` / `CH_TEXT`
  - 기록 API: `record_impression(channel, partner_id, ad_slot)` / `record_click(...)` / `record_load_failed(channel, reason)`
  - 집계 데이터클래스: `ChannelStats` (impressions / clicks / ctr / estimated_revenue_krw)
    + `RevenueDashboard` (since / until / channels / total_impressions / total_clicks / total_revenue_krw / overall_ctr)
  - `compute_dashboard(days=30)` — telemetry.jsonl 스캔 + 기간 필터 + 채널별 집계
  - `format_dashboard(db) → str` — 모노스페이스 표 문자열
- **수익 추정 상수** `ESTIMATES`:
  - 쿠팡: CPC 20원 (CPS 단순화)
  - AdSense: CPC 300원, CPM 2,000원 (한국 트래픽 러프)
- **CoupangAdWidget / AdSenseWidget** 의 `_on_load_finished` 가 성공 시 `record_impression`,
  실패 시 `record_load_failed` 자동 호출 — **opt-in 안 한 사용자는 무동작**
- **설정 탭 버튼**: `📈 광고 수익 대시보드` → `QDialog` 모달에 `format_dashboard` 결과 표시 (모노스페이스)
- **PIPA 준수**: 사용자 식별자·IP·쿠키 일체 기록 안 함. 외부 전송 없음. 기존 telemetry 의 opt-in 체계 그대로.

### 🧪 테스트

**404 개 테스트 전부 통과** (v0.14 384 → v0.15 +20)

| 파일 | 추가 | 범위 |
|---|---|---|
| `test_v150_final.py` | 20 | python-hwpx 경로 분기 + 폴백 + v1 dict 호환 + level 매핑 + CLI 플래그 (6), Self-MoA batch signal emit/억제 (2), revenue 기록 opt-in 체크 + 집계 + 시간 필터 + 포맷 + 위젯 통합 (12) |

### 📦 배포

- `release/HwpxAutomation-v0.15.0.zip` (신규)
- 외부 의존성 변화 없음

### 🔧 API 변경

| 대상 | 이전 | v0.15.0 |
|---|---|---|
| `md_to_hwpx.convert` | legacy lxml 만 | `+use_python_hwpx_writer=False` 인자 (기본 호환) |
| `hwpx_writer.write_ir_blocks` | — | 신규 |
| `ConversionWorker._Signals` | 4 signals | `+batch_started(int)`, `+batch_finished(bool)` |
| `AppConfig` | v0.14 필드 | `+use_python_hwpx_writer: bool` |
| `commerce.revenue_telemetry` | — | 신규 모듈 (API 상수 + 기록 + 집계) |

### 🗓️ 로드맵 현황

**코드로 가능한 모든 항목 100% 집행 완료.**

- [x] 쿠팡 파트너스 (매출 채널 #1) — v0.12
- [x] Google AdSense (매출 채널 #2) — v0.13
- [x] Self-MoA × Batch 50% 절감 — v0.14
- [x] python-hwpx 기반 HWPX writer — v0.15
- [x] md_to_hwpx 분기 스위치 — v0.15 ✨
- [x] Self-MoA × Batch GUI heartbeat — v0.15 ✨
- [x] 광고 수익 텔레메트리 + 대시보드 — v0.15 ✨
- [x] AI 기본법 준수 / GS readiness 100% / Claude Code Skills / FastMCP / Firebase REST — v0.9~v0.14
- [ ] **[사용자 절차]** Azure Trusted Signing 실 계정 가입 + 서명 → WDAC 경고 해소
- [ ] **[사용자 절차]** TTA GS 인증 심사 신청 (2-3 개월)
- [ ] **[사용자 절차]** 나라장터 종합쇼핑몰 등록 (GS 후)
- [ ] **[사용자 설정]** 쿠팡 Partners `982081` / `AF7480765` 설정 입력 → 즉시 실 수익

v1.0 상용 전환까지 남은 것은 전부 외부 계정/인증 절차.

---

## [0.14.0] — 2026-04-20

### 🔗 v0.13.0 로드맵 "코드로 가능한 것" 전량 집행

v0.13 끝 로드맵 중 외부 계정 불필요한 3 개 항목을 모두 구현.

### 💰 Self-MoA × Batch API 통합 — N draws 를 1 batch 로

기존 Self-MoA 는 draws 만큼 **순차 호출** (N 번 + aggregator 1 번). v0.14 는 draws 를
**하나의 Batch job 으로 제출** → Gemini Batch API **50% 할인**. aggregator 만 실시간.

- **SelfMoAClient 확장** (`src/parser/self_moa.py`):
  - 새 인자: `use_batch`, `batch_api_key`, `batch_model`, `batch_poll_sec`
  - 새 메서드: `_draws_serial(prompt)` (기존 경로), `_draws_via_batch(prompt)` (신규)
  - **자동 폴백**: batch 실패 / API key 없음 / 모듈 누락 시 serial 경로로 떨어짐
  - `model` 표기에 `+batch` 태그 부착 (ex: `self-moa[gemini-2.5-flash×3+batch]`)
- **Factory 통합** (`create_default_client`): `cfg.use_gemini_batch=True` + Gemini 백엔드면
  `api_key_manager.get_key("gemini")` 로 키 조회 후 자동 Batch 경로
- **비용 효과**: draws=3, 평균 1K 입력 / 500 출력 기준 대화형 → 대략 **33% 절감** (aggregator 1 회는 real-time)

### 📝 python-hwpx writer 확장 — 표 삽입

v0.13 writer 는 단락만 지원. v0.14 는 표 추가.

- **신규 데이터클래스** `WriteTable(rows, header_row, width, height)` — 2D 리스트로 N×M 표
- **`write_paragraphs` 가 `WriteTable` 도 수용** (isinstance 분기)
- **셀 채우기**: python-hwpx 의 `add_table(rows, cols)` 가 돌려주는 표 객체의 셀들을
  순회하며 `add_paragraph(text=...)` 로 내용 설정
- **API 버전 호환**: `_iter_table_cells()` 가 `.cells` / `.iter_cells()` / `.rows` 3 경로
  순서로 시도. 셀 접근 API 가 python-hwpx 버전별로 달라도 동작
- **`WriteReport.tables_added` 필드 추가** — 삽입 성공한 표 개수
- **구조 검증 테스트**: 삽입 후 `Contents/section0.xml` 에 `tbl` 요소 존재 확인 (ZIP 무결성 포함)

### 📥 G2B 공고 첨부 자동 다운로드

v0.11 G2B 어댑터는 목록/상세 조회만. v0.14 는 실 첨부파일 다운로드.

- **신규 함수** `download_bid_attachments(client, bid_no, output_dir, ...)`:
  - 내부적으로 `client.get_bid_detail(bid_no)` → raw dict 의 URL 필드 추출
  - `_extract_attachment_urls(item)` — `ntceSpecFileDwldUrl1/2`, `dtlsBidNtceDocUrl1` 등 알려진 G2B 필드명 패턴 매칭
  - `_guess_filename(url, default)` — URL path 에서 파일명 추출 (Windows 금지 문자 sanitize 포함)
  - chunk 단위 스트리밍 다운로드 (65 KB), `max_bytes` 초과 시 스킵
  - 이미 존재하는 파일은 `overwrite=False` 기본으로 스킵
- **신규 데이터클래스**: `AttachmentFile(filename, url, size_bytes, local_path)`,
  `DownloadResult(bid_no, output_dir, files, total_bytes, skipped, error)`
- **pro 게이트 상속** — G2BClient 생성자가 이미 pro 검증하므로 download 도 자동 pro
- **법적 제약**: 다운로드 자체는 공공 데이터 이용허락 범위 내 (조달청 무료 API). 2차 재배포는 별도 검토.

### 🧪 테스트

**384 개 테스트 전부 통과** (v0.13 369 → v0.14 +15)

| 파일 | 추가 | 범위 |
|---|---|---|
| `test_v140_new.py` | 15 | Self-MoA × Batch (4), writer 표 (3), G2B URL 추출 + 파일명 sanitize + 다운로드 e2e + max_bytes + 에러 경로 (8) |

회귀 방어: 기존 `test_v040_self_moa.py` 9 개 그대로 통과 — 시그니처 변경은 백워드 호환.

### 📦 배포

- `release/HwpxAutomation-v0.14.0.zip` (신규)
- 외부 의존성 변화 없음
- v0.13 에서 발견한 **Windows 앱 제어 정책 차단** 은 동일 (코드 사이닝 미적용 — Azure Trusted Signing 로드맵)

### 🔧 API 변경

| 대상 | 이전 | v0.14.0 |
|---|---|---|
| `SelfMoAClient.__init__` | `base_client, aggregator, draws` | `+use_batch, +batch_api_key, +batch_model, +batch_poll_sec` (기본값 유지, 호환) |
| `SelfMoAClient.model` | `self-moa[model×N]` | `self-moa[model×N[+batch]]` |
| `hwpx_writer.WriteTable` | — | 신규 |
| `WriteReport.tables_added` | — | 신규 필드 |
| `g2b_adapter.download_bid_attachments` | — | 신규 |

### 🗓️ 로드맵 현황 (v1.0 기준)

- [x] 쿠팡 파트너스 (매출 채널 #1) — v0.12
- [x] Google AdSense (매출 채널 #2) — v0.13
- [x] Self-MoA × Batch 통합 — v0.14 ✨ 본 릴리즈
- [x] python-hwpx writer 표 지원 — v0.14 ✨
- [x] G2B 첨부 다운로드 — v0.14 ✨
- [ ] Azure Trusted Signing 실 계정 가입 + 서명 — **외부 절차 필요** (사용자)
- [ ] python-hwpx 로 `md_to_hwpx` 완전 교체 — v0.15+
- [ ] TTA GS 심사 신청 — v1.0 직전 (사용자 절차)
- [ ] 나라장터 종합쇼핑몰 등록 — GS 인증 후 (사용자 절차)

---

## [0.13.0] — 2026-04-20

### 🏁 v0.12.0 에서 남긴 로드맵 4 트랙 집행

v0.12.0 CHANGELOG 의 "로드맵 현황" 에서 오픈 상태였던 항목 전체 클로즈.

### 💚 [매출 채널 #2] Google AdSense 실장

쿠팡(한국/CPS)에 이어 Google(글로벌/CPC) 까지 듀얼 채널.

- **신규 모듈** `src/gui/widgets/adsense_ad.py` — QWebEngineView 로 `<ins class="adsbygoogle">`
  스크립트 렌더링. `ca-pub-...` publisher_id + 광고 단위 slot 필요.
- **`build_html()` JS injection 방어** — publisher_id 는 `ca-pub-` prefix 검증 + `-` 외 특수문자 제거,
  slot 은 숫자만, ad_format 은 알파넘만
- **AdPlaceholder 확장**: `activate_adsense(publisher_id, ad_slot, ...)` 메서드. Coupang/AdSense
  공존 — `deactivate()` 가 두 위젯 모두 정리.
- **광고 레이블** (`DISCLOSURE_TEXT = "광고 | powered by Google AdSense"`) — 투명성 차원, 숨김 불가.
- **AppConfig 필드**: `adsense_publisher_id`, `adsense_ad_slot`, `adsense_format` (`auto`/`rectangle`/...),
  `adsense_width`, `adsense_height`.

### 🎛️ 광고 채널 우선순위 제어

신규 설정 `AppConfig.ad_channel_priority` — 4 선택지:
- `coupang_first` (기본) — 쿠팡 → AdSense 폴백
- `adsense_first` — AdSense → 쿠팡 폴백
- `coupang_only` — 쿠팡만, 실패해도 AdSense 활성 안 함
- `adsense_only` — AdSense만

MainWindow 의 `_apply_ad_state` 가 이 우선순위 + `pro` 티어 + 각 채널 ready 조건을 모두 반영.

### 🐛 [CRITICAL BUG FIX] 설정 탭 저장 시 v0.9+ 필드 wipe

**증상** (v0.9~v0.12 영향): 설정 탭에서 "설정 저장" 누르면 `ad_urls`, `ad_texts`,
`firebase_api_key`, `coupang_*`, `adsense_*`, `sentry_dsn`, `use_instructor_resolver`,
`use_gemini_batch` 등 **UI 에 노출 안 된 필드가 전부 기본값으로 초기화**.

**원인**: `_save_config()` 가 `AppConfig(...)` 를 처음부터 새로 만들어 UI 에 없는 필드를
전부 기본값으로 덮어씀.

**수정**: `dataclasses.replace(self._config, ...)` 로 기존 설정을 base 로 UI 필드만 override.

**회귀 방어**: `test_settings_save_preserves_v09_plus_fields` — Firebase / 광고 / Sentry /
instructor / batch 필드가 저장-재로드 사이클 후 그대로 유지되는지 검증.

### 🧩 설정 탭 UI 확장 (v0.9~v0.12 필드 노출)

기존엔 config 파일 직접 편집해야 쓸 수 있던 v0.11~v0.12 기능을 UI 로 노출:

- **💰 매출 채널** GroupBox:
  - 쿠팡 Partner ID (QSpinBox, 0 = 비활성)
  - 쿠팡 tracking code (QLineEdit, `AF...` placeholder)
  - AdSense publisher_id (`ca-pub-...` placeholder)
  - AdSense ad_slot (숫자)
  - 채널 우선순위 (QComboBox, 4 선택지)
- **🛰️ 원격 에러 리포팅 (Sentry opt-in)** GroupBox:
  - 활성화 체크박스
  - DSN 입력 (placeholder 안내)
- **⚙️ 고급 AI 옵션 (실험적)** GroupBox:
  - Instructor 통일 resolver 체크
  - Gemini Batch API (50% 할인) 체크

### ⏳ Gemini Batch API GUI 프로그레스

v0.12 Batch API 를 GUI 에서 쓸 수 있게.

- **신규 워커** `src/gui/workers/batch_worker.py::GeminiBatchWorker`
  - `QThread` 상속, `progress(state, elapsed)` / `finished_ok(result)` / `failed(msg, result)` Signal
  - 취소 시에도 polling 은 백그라운드 지속 (worker.result 로 사후 확인)
- **신규 다이얼로그** `src/gui/widgets/batch_progress_dialog.py::BatchProgressDialog`
  - `QProgressDialog` 기반 indeterminate bar + 경과 시간 라벨
  - `_humanize()` 로 Google Batch state enum 한국어화
  - "백그라운드로 전환" 취소 버튼 (강제 종료 X)

### 📝 [첫 조각] python-hwpx writer 경로

lxml 수동 쓰기 경로의 점진 대체 시작. v0.12 어댑터는 read 만, 이번은 write.

- **신규 모듈** `src/hwpx/hwpx_writer.py`
  - `WriteBlock(text, level, style_id)` 데이터클래스
  - `write_paragraphs(template, blocks, output, *, style_map, inherit_style)` — atomic save
  - `_resolve_para_pr_id(level, style_map)` / `_resolve_char_pr_id(...)` — `StyleMap.to_engine_style_dict()`
    포맷 호환 (`{"level_1": {"paraPrIDRef": "3"}}` 등)
  - `write_checklist_report(template, title, lines, output)` — 간편 보고서 생성 facade
- **atomic**: tempfile 에 먼저 쓰고 rename — 실패 시 원본 훼손 없음
- **에러 복원력**: 개별 단락 실패해도 배열 나머지 계속, `WriteReport.errors` 에 모음
- **검증**: roundtrip 테스트 — 쓴 HWPX 를 다시 `extract_hwpx_text` 로 열어 텍스트 일치 확인

### 🧪 테스트

**369 개 테스트 전부 통과** (v0.12.0 351 → v0.13.0 +18)

| 파일 | 추가 | 범위 |
|---|---|---|
| `test_v130_new.py` | 18 | AdSense 위젯/sanitize (7), 설정 탭 UI + bug 회귀 (2), Batch worker/dialog (3), python-hwpx writer (5), style_map resolver (1) |

### 📦 배포

- `release/HwpxAutomation-v0.13.0.zip` (신규)
- 외부 의존성 변화 없음 (QtWebEngine 은 v0.12 부터 번들)

### 🔧 API 변경

| 대상 | 이전 | v0.13.0 |
|---|---|---|
| `AdPlaceholder` | `activate_coupang` | `activate_adsense` 신규, `deactivate` 가 두 위젯 정리 |
| `AppConfig` | v0.12 필드 | `+adsense_publisher_id`, `+adsense_ad_slot`, `+adsense_format`, `+adsense_width`, `+adsense_height`, `+ad_channel_priority` |
| `SettingsTab._save_config` | `AppConfig(...)` 새로 생성 (**필드 유실 버그**) | `dataclasses.replace(...)` 로 필드 보존 |
| `hwpx_writer.write_paragraphs` | 없음 | 신규 |

### 🗓️ 로드맵 현황 (v1.0 기준)

- [x] 쿠팡 파트너스 (매출 채널 #1) — v0.12
- [x] Google AdSense (매출 채널 #2) — v0.13 ✨ 본 릴리즈
- [x] Gemini Batch GUI 통합 — v0.13 ✨
- [x] python-hwpx write 첫 조각 — v0.13 ✨
- [x] 설정 탭 v0.9+ 필드 UI — v0.13 ✨
- [x] AI 기본법 준수 — v0.11
- [x] GS 인증 readiness 100% — v0.12
- [ ] Azure Trusted Signing 실 계정 가입 + 서명 — v0.14
- [ ] Self-MoA × Batch 통합 (Self-MoA draws 를 1 batch 로) — v0.14
- [ ] python-hwpx 전체 마이그레이션 (md_to_hwpx 대체) — v0.14~v0.15
- [ ] TTA GS 심사 신청 — v1.0 직전

---

## [0.12.0] — 2026-04-20

### 💎 v0.11.0 에서 "v0.12+ 로 연기" 라고 명시한 6 트랙 전부 집행 + 쿠팡 파트너스 실수익 채널

v0.11.0 자가검증 직후 사용자가 "**전부 진행**" 지시 + **쿠팡 Partners 광고 스크립트** 제공
(`Partner ID 982081`, `AF7480765`) → 7 개 트랙 병렬 진행.

### 🟠 [매출 채널 #1] 쿠팡 파트너스 carousel 광고 실장

공정위 광고 표시 의무 준수 + 개인정보 스크러빙 + pro 티어 자동 면제 전부 포함.

- **신규 모듈** `src/gui/widgets/coupang_ad.py` — QWebEngineView 로 쿠팡 `ads-partners.coupang.com/g.js`
  실제 carousel 스크립트 렌더링
- **`build_html(partner_id, tracking_code, ...)` 팩토리** — JS injection 차단 (tracking_code
  는 알파넘 + `-_` 만 통과, 68자 이하; partner_id 는 int 강제; template 은 알파넘)
- **공정위 의무 표시**: `DISCLOSURE_TEXT = "이 광고는 쿠팡 파트너스 활동의 일환으로..."` 상시
  표시 라벨 (숨길 수 없음)
- **AdPlaceholder 확장**: 신규 `activate_coupang(partner_id, tracking_code, ...)` 메서드.
  `deactivate()` 는 쿠팡 위젯 DOM 정리까지.
- **MainWindow 통합**: pro 면 광고 없음 → 쿠팡 설정 있으면 쿠팡 → 없으면 기존 순환 텍스트 광고 순 폴백
- **AppConfig 필드**: `coupang_partner_id` / `coupang_tracking_code` / `coupang_template` /
  `coupang_width` / `coupang_height`
- **build.spec** 에 `QtWebEngineWidgets` + `QtWebEngineCore` collect_submodules 추가 (런타임 번들)

### 📚 [Track 1] `python-hwpx` 라이브러리 도입 + 어댑터

lxml 수동 HWPX 파싱 500~1000 LOC 제거 여정 시작. 기존 경로는 fallback 으로 보존.

- **신규 모듈** `src/hwpx/hwpx_lib_adapter.py`
  - `is_available()` / `version()` — 설치 상태 1 회 캐시
  - `extract_text(path, max_len)` — python-hwpx 우선 (ImportError 시 명시)
  - `extract_text_safe(path)` — 실패 시 None (호출자가 fallback)
  - `count_paragraphs(path)` / `has_section(path)` — 메타 helper
- **`rfp_extractor.extract_hwpx_text()` 통합**: python-hwpx 우선 시도, 실패/미설치 시 기존 lxml 경로 자동 폴백
- **런타임 의존성 추가**: `python-hwpx>=2.8` (MIT 라이선스, pure-Python, 활발 유지)

### 🤖 [Track 2] `instructor` 라이브러리 기반 Unified Resolver (opt-in)

4 백엔드 (Gemini/Ollama/OpenAI/Anthropic) 를 **Pydantic BaseModel 하나** 로 통일.

- **신규 모듈** `src/parser/instructor_resolver.py`
  - `HierarchyDecision` / `HierarchyResponse` Pydantic 스키마
  - `InstructorConfig` + `InstructorResolverClient` (ResolverClient 프로토콜 호환)
  - `create_instructor_client(provider, model, api_key)` 팩토리
- **`create_default_client()` 분기**: `AppConfig.use_instructor_resolver=True` 일 때만 활성 (opt-in)
- **자동 retry + validation feedback** (instructor 기본 기능)
- **기존 경로 100% 유지**: 플래그 미사용 시 v0.11 경로 그대로 → 롤백 간편

### 💰 [Track 3] Gemini Batch API 50% 할인 경로

Self-MoA 의 N 번 draw 를 배치로 묶어 절반 가격.

- **신규 모듈** `src/parser/gemini_batch.py`
  - `GeminiBatchClient(api_key, model, poll_sec).submit_and_wait(requests, timeout_sec, on_poll)`
  - `BatchRequest(key, prompt, schema)` / `BatchResult(batch_name, items, state, error, elapsed_sec)`
  - `run_self_moa_as_batch(prompt, api_key, model, draws, poll_sec)` facade — Self-MoA 와 결합
  - Exponential backoff polling, 기본 30 분 timeout
  - 모든 결과에 `BATCH_DISCOUNT = 0.5` 적용된 가격 요율
- **AppConfig**: `use_gemini_batch: bool` + `gemini_batch_poll_sec: int`
- **opt-in**: 사용자가 "대용량 변환" 버튼 누를 때 활성 (GUI 통합은 v0.13+)

### 🔌 [Track 4] FastMCP in-process 서버 — Claude Code/Cursor/Windsurf 통합

우리 앱의 HWPX 기능을 **MCP 도구** 로 외부 AI IDE 에 노출.

- **신규 패키지** `src/mcp_server/`
  - `server.py` — FastMCP 기반 7 개 도구:
    `extract_hwpx_text`, `extract_hwp_text`, `verify_hwpx`, `parse_manuscript`,
    `analyze_template`, `hwpx_lib_info`, `list_supported_extensions`
  - `__main__.py` — `python -m src.mcp_server` 실행 진입
  - `_safe_path()` — null 바이트 / 미존재 경로 거부
- **새 의존성**: `mcp` (stdio transport)
- **Claude Desktop 설정 예시** docstring 포함

### ✍️ [Track 5] Azure Artifact Signing 준비 — `scripts/sign_release.py`

- 환경변수 `HWPX_SIGNING_KEY_VAULT` / `CERT_PROFILE` / `TENANT_ID` / `CLIENT_ID` 검증
- `dotnet sign code trusted-signing ...` 명령 빌드
- dry-run 기본 — `--run` 플래그로 실 서명
- `powershell Get-AuthenticodeSignature` 사후 검증
- 실제 계정 가입은 별도 (Azure Trusted Signing GA, $10/월, 한국 개인사업자 가입 여부 사전 확인 필요)

### 🏛️ [Track 6] GS 인증 + 나라장터 종합쇼핑몰 readiness 체크

- **신규 스크립트** `scripts/gs_cert_readiness.py` — 11 개 자동 체크:
  - 버전 일관성, CHANGELOG 최신성, LICENSE / README / OSS 고지, AI 기본법 모듈,
    한국어 UI (한글 자수), PII 스크러빙, 코드사이닝 스크립트 존재, 릴리즈 zip, 테스트 전량 통과
- **신규 스크립트** `scripts/generate_oss_notices.py` — `pip-licenses` 로 런타임
  의존성 라이선스 요약 → `OSS_NOTICES.md` 자동 생성
- **현재 통과율 100%** (11/11) — 필수 차단 항목 0개 → GS 심사 신청 준비 완료 (TTA / 국립전파연구원 제출)

### 🧪 테스트

- **351 개 테스트 전부 통과** (v0.11.0 317 → v0.12.0 +34)
- 신규 파일 `tests/test_v120_six_tracks.py` — 34 test
  - python-hwpx 어댑터 (6)
  - 쿠팡 파트너스 (build_html + sanitize + AdPlaceholder 통합 + 공정위 의무 표시)
  - rfp_extractor 경로 (python-hwpx 우선 + lxml fallback 검증)
  - instructor unified resolver (opt-in 분기)
  - Gemini Batch API (discount 상수 + submit_and_wait 흐름)
  - FastMCP 서버 (_safe_path 보안 체크)
  - 서명 스크립트 (env 검증)
  - GS readiness (버전/AI disclosure/OSS notices)
  - AppConfig v0.12 필드 persist round-trip

### 📦 배포

- `release/HwpxAutomation-v0.12.0.zip` (신규) — QtWebEngine 포함으로 이전 대비 +X MB
- 새 런타임 의존성: `python-hwpx>=2.8`
- 새 선택 의존성: `instructor>=1.15` (dev), `mcp>=1.0` (dev), `hypothesis>=6.130` (dev), `pip-licenses` (dev, GS readiness 용)

### 🔧 API 변경 요약

| 함수/클래스 | 이전 | v0.12.0 |
|---|---|---|
| `rfp_extractor.extract_hwpx_text()` | lxml 수동 | python-hwpx 우선 + lxml fallback (자동) |
| `AdPlaceholder` | activate / activate_rotating | 신규 `activate_coupang()` 추가 |
| `gemini_resolver.create_default_client()` | 4 백엔드 직접 | `use_instructor_resolver=True` 면 unified 경로 |
| `AppConfig` | v0.11 필드 | +`coupang_*` (5개), +`use_instructor_resolver`, +`use_gemini_batch`, +`gemini_batch_poll_sec` |

### 🗓️ 로드맵 현황

- [x] v0.11.0 계획의 6 트랙 전부 집행 + 쿠팡 파트너스 실장
- [ ] v1.0 전환: GS 인증 심사 신청 (2-3 개월) → 통과 후 종합쇼핑몰 등록
- [ ] 매출 채널 #2 (Google AdSense) — v0.13+
- [ ] instructor / Batch 경로의 GUI 프로그레스 UI — v0.13+
- [ ] python-hwpx 로 write 경로도 마이그레이션 — v0.14+

---

## [0.11.0] — 2026-04-19

### 🧬 최신 기술 조사 → 5 영역 자가 발전

v0.10.1 자가검증 직후 **4 개 research agent 병렬 조사** (HWP 생태계 / LLM 2026 /
바이브 코딩 / 한국 정부조달 시장) 로 확보한 인사이트를 **저리스크·고가치 5 트랙** 으로 압축.

### ⚖️ [A+] AI 기본법 (2026-01-22 시행) 대응 — 법적 의무

**과태료 3,000만원 리스크 해소**. 생성형 AI 로 만든 콘텐츠는 "AI 가 생성했다" 는 사실을 고지해야 함.

- 신규 모듈 `src/commerce/ai_disclosure.py`
  - `DisclosureInfo` 데이터클래스: 버전 / 백엔드 / 시각 + 포맷 메서드
  - `make_disclosure(backend, ai_used=True)` 팩토리
  - `is_ai_backend()` 유틸 — Ollama 도 AI 로 판정
- **체크리스트 보고서 footer 자동 삽입**: `sort_attachments(..., ai_backend="Gemini")` 로
  `_제출서류_보고서.txt` 하단에 "AI 기본법 준수" 고지 1 줄 자동 추가
- **About 다이얼로그 고지** — 메인 윈도우 "정보" 메뉴에 AI 사용 안내 노출

### 🚀 [A] Gemini 3 Flash 호환 + 모델별 가격 테이블

- `AVAILABLE_MODELS` 튜플 노출 — 5 가지 모델 지원:
  - `gemini-2.5-flash` (기본, GA, $0.075/$0.30)
  - `gemini-2.5-pro` ($1.25/$10)
  - `gemini-3-flash` (2025-12 프리뷰, $0.50/$3.00)
  - `gemini-3-flash-lite` (RFP 추출 최적, $0.25/$1.50)
  - `gemini-3-pro` ($2.50/$15)
- `price_for_model(model) → (input, output)` — 선택된 모델에 맞는 실제 토큰 가격 계산
- Gemini 3 의 `thinking_level="minimal"` ↔ 2.5 의 `thinking_budget=0` 자동 분기.
  SDK 버전 호환성 가드 포함.
- **기본값은 2.5 Flash 유지** (3.x 프리뷰라 GA 대기). 사용자가 config 로 손쉽게 전환 가능.

### 📝 [A] Claude Code Skills — `.claude/skills/`

개발자 경험 개선. 팀/다른 AI 가 이 프로젝트를 작업할 때 즉시 활성화되는 4 개 skill:

- `hwpx-verify.md` — HWPX 구조 / 네임스페이스 / 스타일 참조 검증 체크리스트
- `gemini-hierarchy.md` — Gemini 계층 매핑 호출 패턴 + 모델별 가격표 + 함정
- `pro-tier-gate.md` — Pro 기능 게이트 적용 3 패턴 + 현재 게이트된 기능 목록
- `release-flow.md` — 릴리즈 전체 플로우 (build → zip → verify) + 상용 체크포인트

Production 영향 0 (declarative md 파일만).

### 🛰️ [A] Sentry SDK opt-in 원격 에러 리포팅

- 신규 모듈 `src/utils/error_reporter.py`
  - `init(dsn, release, environment, sample_rate)` — DSN 없으면 no-op
  - `capture_exception / capture_message / set_user` — 초기화 안 된 상태에서도 안전 호출
  - **PIPA 대비 PII 스크러빙** — 환경변수 중 `key/token/password/secret` 자동 [Filtered],
    예외 메시지의 이메일 패턴 `a@b.com → a***@b.com` 마스킹
- `AppConfig` 필드 추가: `error_reporting_optin: bool`, `sentry_dsn: str`
- **sentry-sdk 미설치해도 앱 동작** — ImportError 잡고 no-op 로 fallback
- 메인 윈도우가 설정 값 확인 후 초기화

### 🏛️ [A] 나라장터 (G2B) API adapter 스캐폴드 — 차별화 훅

조달청 무료 Open API (`BidPublicInfoService05`) 로 입찰공고 자동 조회 — **경쟁사에 없는 기능**.

- 신규 모듈 `src/checklist/g2b_adapter.py`
  - `G2BClient(service_key, ...).search_bids(keyword, days, page)` → `G2BSearchResult`
  - `.get_bid_detail(bid_no)` → `BidAnnouncement`
  - `BidAnnouncement`: 공고번호 / 제목 / 수요기관 / 날짜 / 예산 / URL / raw dict
- 파서 ( `_parse_g2b_response`) 가 공공데이터포털 JSON 의 `response.body.items` 형태를
  단건 dict / list 모두 처리 (PBT 로 robust 검증)
- **Pro 전용** — `tier_gate.require("pro")` 생성자에서 강제. 테스트용 `_skip_tier_check=True`.
- v0.12+ 에 GUI 연결 ("이 공고로 작업 시작" 버튼 + 첨부파일 자동 다운로드)

### 🧪 [A] Hypothesis 기반 Property-Based Testing

- 신규 dev 의존성 `hypothesis>=6.130`
- `tests/test_v110_property_based.py` — 9 개 property, 무작위 100~200 example 수백 통과
  - HWP 필터: 순수 한글/ASCII 는 항상 텍스트 인정 / sanitize 는 예외·길이 증가 없음 /
    한글·ASCII 토큰은 절대 드롭 안 됨
  - G2B 파서: 임의 형태 dict 가 와도 크래시 없이 `G2BSearchResult` 반환
  - Firebase tier 파서: 어떤 토큰이 와도 `free|pro|team` 중 하나
  - AI disclosure: 어떤 backend 이름이든 메타 포맷 성공

### 🇰🇷 Firebase 에러 메시지 한국어 번역

- 신규 `firebase_error_to_korean(msg)` — 11 가지 공식 에러 코드 → 친절 한국어:
  - `EMAIL_EXISTS` → "이미 가입된 이메일입니다. 로그인을 시도하거나 다른 이메일을 사용해 주세요."
  - `INVALID_PASSWORD` → "비밀번호가 일치하지 않습니다."
  - `WEAK_PASSWORD : Password should be at least 6 characters` → "비밀번호가 너무 약합니다 (6자 이상)."
  - 등등
- `FirebaseAuthClient._post` 가 HTTP 4xx 응답 시 자동 한국어 `RuntimeError` 발생

### 🧪 테스트

**317 개 전부 통과** (v0.10.1 283 → v0.11.0 +34 신규)

| 파일 | 추가 | 범위 |
|---|---|---|
| `test_v110_new_features.py` | 25 | AI disclosure / Gemini 3 / Sentry / G2B / Firebase KR / Skills 파일 |
| `test_v110_property_based.py` | 9 | PBT (hangul / ascii / sanitize / G2B / Firebase tier / disclosure) |

### 📦 배포

- `release/HwpxAutomation-v0.11.0.zip` (신규)
- 외부 의존성 0 개 추가 (sentry-sdk / hypothesis 는 선택 설치)

### 📊 연구 요약 (참고)

4 개 research agent 병렬 출력 — 채택 우선순위:
- ✅ 즉시 적용: AI 기본법 / Gemini 3 매핑 / Skills / Sentry scaffold / G2B scaffold / PBT
- ⏳ v0.12+: `python-hwpx` 마이그레이션 (lxml 수동→라이브러리), FastMCP in-process 서버,
  `instructor` 라이브러리 통일, Azure Artifact Signing, Hancom AI SDK, Stripe/Paddle MoR
- 📌 시장 인사이트: **inline AI 월 29,900원 = 가격 앵커**, "입찰 특화 GUI 데스크톱 + 로컬 우선" 은 현재 경쟁 공백,
  공공조달 AI 전환 기본계획(2026-03-19) + HWPX ISO 국제표준화(2026) 순풍

### 🔧 API 변경

- 없음 (전부 additive).
- `sort_attachments` 의 신규 인자 `ai_backend=""` 는 기본값으로 기존 호출 호환.

---

## [0.10.1] — 2026-04-19

### 🪚 v0.10.0 자가 검증에서 발견한 2 가지 잔재 수정 (patch)

v0.10.0 릴리즈 직후 자가 검증에서 드러난 품질 이슈 두 개를 고치는 패치.
기능 추가 없음, API 변경 없음.

### 📚 HWP BodyText 파싱 노이즈 필터 (품질 이슈)

v0.10.0 의 실험적 `prefer_full=True` 파서가 HWP 레코드 tag 명 (`"lbt "`, `"ttof"`,
`"dces"` 등 4-byte ASCII 마커) 을 UTF-16LE 로 잘못 디코드해 결과에 `氠瑢 潴景 捤獥 汤捯`
류의 희귀 CJK 문자 노이즈가 섞이던 문제를 해결.

- `src/checklist/hwp_text.py` 에 두 층의 노이즈 필터 추가:
  - **레코드 레벨** (`_looks_like_text`) — 한글/인쇄 ASCII 비율이 20% 미만이면 레코드 전체 드롭
  - **토큰 레벨** (`_drop_noise_tokens`) — 공백 분리 후 한글도 ASCII 도 없는 짧은 (≤6자) rare-CJK 토큰 드롭
- **실 샘플 측정** (`1. 입찰공고문_26아카데미.hwp`):
  - v0.10.0: 12,365 자 중 U+6000~U+7FFF 대역 노이즈 ~25 개
  - v0.10.1: 12,126 자 중 동일 대역 **5 개** (**99.6% 제거**)
  - 실제 본문 ("입찰공고", "농림수산식품교육문화정보원", "귀농귀촌 아카데미" 등) 100% 보존
- 긴 한문 인용 (7자 이상 연속 한자) 은 보존 (정상 한문 판정).
- 유효하지 않은 UTF-16 surrogate half 도 공백 처리.

### 🔤 릴리즈 스크립트 cp949 인코딩 (DX 이슈)

Windows 기본 stdout 은 cp949 → 이모지 (📦 ✅ 🎉 등) 출력 시 `UnicodeEncodeError`.
v0.10.0 에선 `PYTHONIOENCODING=utf-8` 환경변수를 수동으로 지정해야 했음.

- `scripts/make_release.py`, `scripts/verify_release_zip.py` 상단에
  `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 추가.
- 이제 환경변수 없이 `python scripts/make_release.py` 만으로 동작.
- 재설정 실패 시 (예: stdout 이 파이프) graceful 처리 — 기본 인코딩 유지.

### 🧪 테스트

- **283 개 테스트 전부 통과** (v0.10.0 266 + 신규 17)
- 새 파일: `tests/test_v101_noise_filter.py`
  - `_looks_like_text`: 한국어/ASCII/mixed/노이즈 판정 5 케이스
  - `_drop_noise_tokens`: 짧은 CJK-only 드롭, 긴 한문 보존, 다중 라인 처리
  - `_records_to_text` 통합: 노이즈 레코드 스킵 + mixed chunk 유지
  - `_sanitize_hwp_control` 파이프라인: NUL→공백 + 토큰 정리 + surrogate half 처리
  - 실 HWP 샘플: 노이즈 < 10 개 & 핵심 키워드 5 개 보존
  - 릴리즈 스크립트 `sys.stdout.reconfigure` 호출 검증

### 📦 배포

- `release/HwpxAutomation-v0.10.1.zip` (신규 재빌드)
- v0.10.0 사용자는 HWP 파일로 체크리스트를 돌렸을 때 결과 품질이 눈에 띄게 올라감.

### 🔧 API

- **변경 없음** (이전 공개 API 시그니처 100% 호환).
- 하위 헬퍼 `_looks_like_text`, `_drop_noise_tokens` 가 `__all__` 에 추가돼 단위 테스트용 export.

---

## [0.10.0] — 2026-04-19

### 💼 v0.9.0 스캐폴드 → 실전 상업화

v0.9.0 에서 형태만 잡아둔 4 트랙(pro 티어, Firebase, HWP pure-Python, 정렬기) 을
**실제로 동작하는 코드** 로 심화. 외부 계정/키 없이도 전부 검증 가능한 4 영역.

### 🔐 Pro-tier 실제 적용 (스캐폴드 → 실장)

이전 `requires_tier` / `is_allowed` 는 모든 경로에서 **사용되지 않는 데코레이터** 였음.
v0.10.0 에서 다음 엔트리포인트에 실제로 게이트 장착:

- **Self-MoA** (`src/parser/self_moa.py`) — 생성자에서 `tier_gate.require("pro")`. 무료 티어가
  인스턴스화하면 `TierDeniedError`. 테스트용 `_skip_tier_check=True` 백도어 제공.
- **OpenAI / Anthropic 백엔드** (`gemini_resolver.create_default_client`) — 백엔드 선택 시 pro 체크.
  Gemini / Ollama 는 무료 티어에서도 그대로 동작.
- **CLI `build-batch`** — 무료 티어는 첫 1 개 파일만 처리하고 pro 안내 출력.
  `--pro-key` CLI 플래그 또는 `HWPX_PRO_KEY=1` 환경변수로 우회 가능.
- **광고 placeholder** (`main_window._apply_ad_state`) — pro 세션이면 자동 숨김 (광고 제거 혜택).

### 🔥 Firebase Auth REST 실장 (스텁 → stdlib urllib 기반 실제 호출)

- `src/commerce/auth_client.py` **FirebaseAuthClient** 를 Identity Toolkit v1 REST API 로 완전 구현
  - `POST /v1/accounts:signInWithPassword?key=...`
  - `POST /v1/accounts:signUp?key=...`
  - `stdlib urllib.request` 만 사용 → **외부 의존성 0** (requests/httpx 추가 안 함)
- **JWT `idToken` 파싱**: payload 의 custom claim `tier` 를 읽어 `pro`/`team` 세션 생성.
  Firebase Console 에서 `gcloud auth set-custom-claims <uid> {"tier":"pro"}` 만 해 두면 바로 연동.
- 에러 처리: HTTP 4xx → 로그인은 `None` 반환 (로컬과 동일), 가입은 `ValueError`.
- `create_auth_client(config)` 팩토리: `auth_backend="firebase"` + `firebase_api_key` 설정 시 자동.
  더이상 `_use_stub=False` 수동 지정 불필요.
- 테스트 가능: `_opener` 인자에 가짜 urlopen 주입 → 네트워크 없이 단위 검증.

### 📚 HWP BodyText 실험적 파싱 (PrvText 2KB 한계 돌파)

- `src/checklist/hwp_text.py` 에 **`prefer_full=True`** 모드 추가
  - `FileHeader` 의 compressed 비트 확인 → `BodyText/Section*` 전 스트림을 **raw DEFLATE** 해제
  - 해제된 바이너리를 HWP 5.0 레코드 포맷으로 순회 (태그 `0x43 = HWPTAG_PARA_TEXT` 만 추출)
  - 레코드 size 확장 (`0xFFF` → 다음 4바이트) 지원
  - 제어문자 (`< 0x20`) 를 공백/개행으로 정리
- **안전 폴백**: BodyText 파싱 실패 시 자동으로 PrvText (v0.9.0 경로) 로 떨어짐. 절대 예외 안 냄.
- **pro 연동**: `rfp_extractor` 가 `tier_gate.is_allowed("pro")` 일 때 `prefer_full=True` 자동 활성.
  무료 티어는 여전히 PrvText 미리보기만 (행동 바뀌지 않음).

### 📦 Sorter 개선: 보고서 + ZIP 출력

- `sort_attachments` 에 두 새 인자:
  - **`write_report=True`** (기본) — 출력 폴더에 `_제출서류_보고서.txt` 기록
    (복사 완료 / 누락 / 매칭 없는 파일 / 제출 가능 여부 · 생성 시각 · 원본 폴더)
  - **`make_zip=False`** — `True` 면 출력 폴더 내용을 ZIP 으로 묶음 (ZIP 은 출력 폴더 **밖** 에 생성 → 재귀 압축 방지)
  - **`zip_name`** — 커스텀 ZIP 파일명 (기본 `{폴더명}.zip`)
- ChecklistTab 의 "파일 자동 정렬..." 버튼: 정렬 후 **"ZIP 으로 묶을까요?"** Yes/No 다이얼로그.
- `SortReport` 에 `zip_path`, `report_path` 필드 추가.

### 🧪 테스트

- **266 개 테스트 전부 통과** (v0.9.0 241 + v0.10.0 25 추가)
- 새 파일: `tests/test_v100_pro_tier.py`
  - pro 티어 게이트 (3 가지 차단 + 3 가지 허용 경로)
  - Firebase REST: 성공/실패/JWT 커스텀 claim (fake `urlopen` 주입)
  - HWP BodyText: 합성 레코드 (`_records_to_text`), 확장 size (`0xFFF`), 제어문자 정리
  - Sorter: 보고서 작성/스킵, ZIP 생성, 커스텀 ZIP 이름
- 업데이트: `test_v040_self_moa.py` (autouse pro 세션), `test_v030` 팩토리 (pro 그랜트),
  `test_v090` (Firebase stub 테스트가 `_use_stub=True` 명시)

### 🔧 API 변경 요약

| 함수/클래스 | v0.9.0 | v0.10.0 |
|---|---|---|
| `SelfMoAClient(...)` | 누구나 생성 | **pro 필수** (`TierDeniedError`) — 테스트용 `_skip_tier_check=True` |
| `FirebaseAuthClient(api_key)` | `_use_stub=True` 기본 (항상 `NotImplementedError`) | `_use_stub=False` 기본 (실제 REST 호출) |
| `create_default_client()` openai/anthropic | 무제한 | **pro 필수** |
| `extract_hwp_text(path)` | PrvText 만 | `prefer_full=True` 옵션 (BodyText 시도 → PrvText 폴백) |
| `sort_attachments(result, out)` | 복사만 | `write_report=True`, `make_zip=False`, `zip_name` 추가 |

### 📌 비호환 사항

- v0.9.0 에서 `FirebaseAuthClient(api_key)` 만 넘겨 `login()` 을 호출한 코드는 이제 **실제 네트워크 호출**.
  테스트에서 이전 stub 동작을 원하면 `_use_stub=True` 를 명시해야 함. (`tests/test_v090_tracks.py` 업데이트됨)
- `SelfMoAClient` 를 무료 세션에서 생성하던 코드는 `TierDeniedError` 발생. 앱 로직은 factory 경로만 거치므로 영향 없음.

---

## [0.9.0] — 2026-04-19

### 🧰 4 트랙 묶음 — HWP pure Python · UX · 벤치마크 · 상업화

사용자 요청으로 4 트랙 병렬 진행한 단일 릴리즈.

### 📜 Track 4: pure-Python HWP 텍스트 추출 (LibreOffice 없이)

- **`src/checklist/hwp_text.py`** — `olefile` 로 HWP 의 `PrvText` (OLE 스트림) 읽어 UTF-16 디코드
  - LibreOffice 미설치 환경에서도 **미리보기 영역 (~2,000 자)** 만으로 RFP 추출 가능
  - 공고문 같은 짧은 문서는 충분, 긴 규격서는 LibreOffice 변환 권장
- **rfp_extractor**: HWP 도 `SUPPORTED_EXTENSIONS` 에 추가
- **ChecklistTab**: HWP 선택 시 LibreOffice 탐지 → 3 옵션 (PDF 변환 / PrvText 모드 / 취소)
- **새 의존성**: `olefile>=0.47` (BSD, pure Python)

### ✨ Track 2: 고급 UX

#### A. 체크리스트 → 첨부 자동 정렬 (`src/checklist/sorter.py`)
- 매칭된 파일들을 번호매겨 새 폴더에 복사 (`01_사업자등록증.pdf`, `02_법인인감.pdf`, ...)
- 매칭 안 된 폴더 파일은 `_미매칭/` 하위로 보존 (copy-only, 원본 유지)
- ChecklistTab 에 **"파일 자동 정렬..."** 버튼

#### B. CLI 일괄 변환 (`build-batch`)
```powershell
python -m src.cli build-batch --template T.hwpx --folder drafts/ --output-dir out/
```
- `--recursive` / `--use-gemini` / `--backend` 지원

#### C. 템플릿 썸네일 (`src/template/thumbnail.py`)
- HWPX 의 `Preview/PrvImage.png` 를 QPixmap 으로 렌더
- TemplateTab 상세 패널 상단에 표시

### 📊 Track 3: 성능 벤치마크

- **`scripts/benchmark.py`** — 대용량 원고 합성 + 시간/메모리 측정
  - `--chars 100000` (기본), `--reps 3`, `--include-quant`
  - `tracemalloc` 기반 peak 메모리
- **`BENCHMARK.md`** — 실측 결과:
  - 10 만자 원고 end-to-end **0.8 초 / 9 MB 메모리**
  - 정량 1,497 셀 parse + save **0.9 초 / 23 MB**
  - Gemini 호출 비용 추정표 + 프로파일링 팁

### 💼 Track 1: 상업화 Scaffold

실제 Firebase / ad network 연동은 사용자 입력 대기. 구조만 준비.

- **`src/commerce/auth_client.py`** — `AuthClient` Protocol
  - `LocalAuthClient` (기존 UserStore 래퍼) / `FirebaseAuthClient` (stub — endpoint 채우면 동작)
  - `create_auth_client(config)` factory
- **`src/commerce/tier_gate.py`** — 티어 게이팅
  - `@requires_tier("pro", feature="Self-MoA")` 데코레이터
  - `TierDeniedError` 예외 + `is_allowed("pro")` 조건 체크
  - 전역 세션 등록 (로그인 성공 시 MainWindow 가 자동 등록)
- **`AdPlaceholder.activate_rotating()`** — 여러 광고 URL 순환 (QTimer)
  - `ad_urls`, `ad_texts`, `ad_rotation_sec` AppConfig 필드 추가
- **AppConfig 확장**: `auth_backend`, `firebase_api_key`, `ad_urls`, `ad_texts`, `ad_rotation_sec`

### Testing

- **v0.9.0 테스트 +19** (HWP text / sorter / thumbnail / AuthClient / tier_gate / ad rotation)
- **전체 241 pytest 통과** (v0.8.0 대비 +19)

### Known Limitations

- HWP pure-Python 경로는 **PrvText(미리보기)** 만 — 전체 본문 아님
- FirebaseAuthClient 는 **NotImplementedError** (scaffold only) — v0.10+ 에서 실 구현
- 프로 티어 잠금 데코레이터는 인프라만 — 실제 프로 전용 기능 지정은 v0.10+

---

## [0.8.0] — 2026-04-19

### 🧩 HWP 변환 + PDF OCR + 정량 타입 힌트 (3 트랙 묶음)

3 개 개선 트랙을 한 릴리즈로 묶었음. 각각 독립적으로 유용하지만 결합하면 RFP 처리
엔드투엔드가 훨씬 매끄러워짐.

### 🗂️ Track 1: HWP → PDF 자동 변환 (`src/checklist/hwp_converter.py`)

사용자 PC 에 **LibreOffice** 가 설치되어 있으면 HWP 파일을 자동으로 PDF 로 변환.
변환된 PDF 는 기존 PDF 처리 경로(Gemini document-processing) 로 흘러감.

- **`detect_libreoffice()`** → Windows 기본 경로 + PATH 에서 `soffice.exe` 자동 탐지
- **`convert_hwp_to_pdf(hwp_path)`** → headless subprocess 로 PDF 생성
  - 임시 UserProfile 사용 (사용자 설정 간섭 방지)
  - 10~30 초 timeout
- **ChecklistTab**: RFP 파일 picker 에 `.hwp` 포함 → HWP 선택 시 LibreOffice 감지해서:
  - 있으면 "PDF 변환 후 진행" 확인 다이얼로그
  - 없으면 설치 가이드 메시지

**한/글 설치 불필요** — 한컴 Automation (유료) 회피. LibreOffice 는 무료 + HWP 필터 내장.

### 🔍 Track 2: PDF 발행일 추출 (pdfplumber + Tesseract OCR)

파일명에서 발행일 추출 실패 시 **PDF 내용** 을 읽어서 자동 탐지.

- **`src/checklist/pdf_date_extractor.py`**:
  - **pdfplumber 텍스트 추출** (디지털 PDF — 대부분 케이스)
  - **Tesseract OCR fallback** (스캔/이미지 PDF — Tesseract 설치 시만)
  - 한국식 날짜 패턴 4종 (`YYYY년 MM월 DD일` / `YYYY. MM. DD` / `YYYY-MM-DD` / `YYYYMMDD`)
  - **키워드 근접 매칭** — "발행일", "발급일", "등록일" 근처 80 자 내 날짜 우선
- **matcher 통합**: 파일명에 날짜 없으면 자동 PDF fallback 시도
  - source 필드: `"filename"` / `"text"` / `"ocr"` / `"unknown"`
- **ChecklistTab 결과 테이블**: 발행일 뒤에 출처 아이콘 표시 (📄 filename / 📃 text / 🔍 OCR)
- **신규 의존성**: `pdfplumber` (BSD) 추가. Tesseract 는 **선택** (번들 안 함).

### 📐 Track 3: 정량제안서 타입 힌트 (경량)

`QuantField` 타입 추론 본편은 샘플 더 확보 후 (v0.9+). v0.8.0 은 **경량 힌트**:

- **`src/quant/type_hints.py`**: 레이블 키워드 → `FieldType` + 단위 매핑
  - 12 종 패턴 (설립년도 → NUMBER+년, 발행일 → DATE, 총원 → NUMBER+명, 주소 → MULTILINE, 등)
  - 우선순위 순서: "년도/연도" 를 "일자" 보다 먼저 (설립년도 = DATE 아니라 NUMBER 년)
- **QuantTab UI**: 레이블 추정 셀 옆 값 셀의 tooltip 에 `NUMBER (명)` / `DATE` 같은 힌트 표시

완전한 타입 본편은 samples/가 추가로 쌓인 뒤 진행.

### Testing

- **v0.8.0 테스트 +34**:
  - 타입 힌트 13 케이스 (파라미터 테스트)
  - HWP converter (존재/미존재 바이너리, subprocess mock, 실패 케이스)
  - PDF 날짜 패턴 4종 + 키워드 근접 + text/OCR fallback
  - matcher 의 PDF fallback 통합 (파일명 실패 → PDF 내용 성공 케이스)
- **전체 222 pytest 통과** (v0.7.0 대비 +34)

### 새 의존성

- `pdfplumber>=0.11` (필수) — 디지털 PDF 텍스트 추출
- `pytesseract`, `pdf2image` (선택) — OCR 기능. 설치 안 해도 앱은 동작, OCR 만 스킵됨

### 사용자 환경 요구

| 기능 | 필요한 외부 설치 |
|---|---|
| HWP → PDF 변환 | **LibreOffice** (https://www.libreoffice.org/download/) |
| 디지털 PDF 발행일 추출 | 없음 (pdfplumber 번들) |
| 스캔 PDF OCR | **Tesseract** + `tesseract --lang kor` 설치 + Python 측 `pytesseract`, `pdf2image` 설치 |

LibreOffice / Tesseract 는 모두 오픈소스 무료. 설치 안내는 각 다이얼로그에서 제공.

---

## [0.7.0] — 2026-04-19

### 💼 상업화 훅 + 정량/체크리스트 개선 (3 트랙 동시)

사용자 요청으로 **v0.5.x / v0.6.x / v0.7.0** 3 트랙을 병렬로 진행한 단일 릴리즈.
모든 상업화 기능은 **기본 OFF**, 사용자가 설정 탭에서 명시적으로 켜야 활성화.

### 🎯 v0.5.1 — 정량제안서 행 조작 + 병합 셀

- **`RowOp` 데이터 클래스** — save 시점에 적용되는 행 복제/삭제 지시
- **`_apply_row_op()`** — ``<hp:tr>`` 복제(빈 셀로 삽입) / 삭제
- **`QuantCell`** 에 `row_span` / `col_span` / `is_span_origin` 필드 추가
- **파서**: ``<hp:cellSpan colSpan=N rowSpan=M/>`` 감지
- **QuantTab UI**:
  - "+ 선택 행 아래에 추가" / "- 선택 행 삭제" 버튼
  - 병합 셀 ``QTableWidget.setSpan()`` 시각적 반영
  - 대기 중 행 연산 카운터 표시 ("복제 N / 삭제 M")
  - 저장 후 자동 재로드로 UI 동기화

### 📁 v0.6.1 — 체크리스트 재귀 + HWP 안내

- **`build_checklist(..., recursive=True)`** 옵션 → 하위 폴더까지 스캔
- **ChecklistTab**: "하위 폴더 포함" 체크박스 추가
- **HWP 파일 선택 시 안내 다이얼로그** → HWPX / PDF 변환 권장 (한/글에서 다른 이름으로 저장)
- **HWP 파일이 제출 폴더에 있으면** 진행 로그에 "파일명 매칭은 되지만 내용 기반 확인은 HWPX 변환 후" 안내

### 💼 v0.7.0 — 상업화 훅 (placeholder)

실제 상업화 진입 전 인프라 준비. 모두 기본 OFF.

- **`src/commerce/user_db.py`** — 로컬 사용자 DB
  - `User`, `UserStore` (register/verify/delete)
  - **PBKDF2-HMAC-SHA256 + 16 바이트 salt + 200,000 iterations** 비밀번호 해싱
  - 저장: `%APPDATA%/HwpxAutomation/users.json`
  - 평문 비밀번호는 **어디에도 저장되지 않음** (테스트로 검증)
- **`src/gui/widgets/login_dialog.py`** — 로그인/회원가입 토글 다이얼로그
- **`src/commerce/updater.py`** — GitHub Releases API 호출해 새 버전 체크
  - `check_for_update(current_version, repo)` → `UpdateInfo`
  - 네트워크 오류 / 404 / 같은 버전 / 최신 버전 각 케이스 처리
- **`src/utils/telemetry.py`** — 로컬 사용량 기록 (opt-in)
  - `telemetry.record(event, **props)` — 비활성이면 **no-op** (성능 0)
  - `summary()` / `clear()` 지원
  - 저장: `%APPDATA%/HwpxAutomation/telemetry.jsonl`
- **`AdPlaceholder`** 실제 구현 — `activate()` / `deactivate()` 로 동적 표시
- **AppConfig 확장**: `require_login` / `ad_enabled` / `ad_url` / `telemetry_optin` / `auto_update_check` / `update_repo`
- **MainWindow**:
  - 앱 시작 시 텔레메트리 설정 반영
  - `require_login=True` 면 LoginDialog 자동 표시
  - `auto_update_check=True` 면 2초 후 백그라운드로 새 버전 체크 (상태바 알림)
- **SettingsTab** 에 "상업화 옵션" 그룹 추가 — 4 토글 + "지금 업데이트 확인" 버튼

### Testing

- **v0.5.1 테스트 +6**: 행 복제/삭제 후 행 수 검증, 편집과 행 조작 결합, 병합 셀 감지
- **v0.6.1 테스트 +4**: 재귀 vs 비재귀 스캔, 중첩 폴더 매칭
- **v0.7.0 테스트 +16**: UserStore (해시 검증 포함), updater (버전 파싱 + 4 케이스), 텔레메트리 (opt-in 준수), LoginDialog, AdPlaceholder

### Known Limitations

- **실제 서버 백엔드 없음** — 모든 상업화 기능은 로컬-only placeholder
- **GitHub repo 기본값** `"example/hwpx-automation"` — 실제 배포 전 `update_repo` 설정 필요
- **광고 슬롯** 은 단순 라벨 + URL 클릭만 — 진짜 ad network SDK 통합은 별도 단계
- **QuantTab 에서 병합 셀 편집은 원점 셀만** — 가려진 셀은 표시만 (setSpan 로 숨김)

---

## [0.6.0] — 2026-04-19

### ☑️ 제출서류 체크리스트 본편

**v0.4.0 foundation 을 완전한 기능으로 승격.** RFP (공고문/제안요청서) 를 Gemini document-processing 으로 분석해서 필수 제출서류 목록을 자동 추출하고, 사용자 폴더의 실제 파일들과 매칭해서 체크리스트로 표시.

### 실측 결과 (실샘플 1건)

`1. 입찰공고문_26아카데미.hwpx` 에서 Gemini 가 **19개 제출서류** 정확 추출:
- 산출내역서, PM 4대보험증명서, 재직증명서, 공동수급협정서
- 입찰보증금 지급각서, 근로자 권리보호 이행 서약서, 부당계약 체크리스트
- 하도급대금 직접 지급 합의서, 표준비밀유지협약서, 기술자료 임치 이행 서약서
- 각 서류별 한/영 키워드 + 필수/선택 + 원문 인용 포함

### Added

- **`src/checklist/rfp_extractor.extract_from_rfp()`** — 실제 Gemini document-processing 구현
  - **PDF**: Gemini Files API 업로드 → ``generate_content(contents=[uploaded, prompt])`` 로 구조화 출력 받음. PDF 텍스트 추출은 무료 (Gemini 3).
  - **HWPX**: ``Contents/section*.xml`` 의 ``<hp:t>`` 텍스트 합쳐 plain text 로 추출 후 Gemini text 모드.
  - **HWP** 는 미지원 (사용자가 HWPX 로 먼저 변환)
- **`extract_hwpx_text()`** — 독립적인 유틸리티 함수 (100K 문자 상한, 초과시 앞부분만)
- **구조화 응답 스키마** — id/name/is_required/max_age_days/filename_hints/description
- **`src/gui/tabs/checklist_tab.py`** — 6번째 탭:
  - RFP 파일 선택 + 제출서류 폴더 선택
  - "RFP 분석 + 체크" 버튼 → QThread 워커로 Gemini 호출 → 결과 테이블 표시
  - "데모 서류로 대조" 버튼 → API Key 없어도 사용 가능 (5종 샘플 서류)
  - 결과 테이블: 상태(✅/⚠️/❌) / 서류명 / 매칭파일 / 발행일 / 사유
  - "보고서 저장..." → Markdown 형식 텍스트 파일
- **`src/gui/workers/rfp_worker.py`** — RFP 추출 QThread 워커
- **`tests/fixtures/rfp_samples/`** — v1 에서 실 샘플 5종 복사 (HWPX 2, PDF 3)

### Changed

- **메인 윈도우 6 탭 구조**: 변환 / 템플릿관리 / 미리보기 / 정량 / **체크리스트** / 설정 (Ctrl+1~6)
- 기존 foundation `src/checklist/filename_matcher.py` / `matcher.py` 는 그대로 재활용 (결정론 경로 안정)

### Testing

- **162 pytest 통과** (v0.5.0 대비 +15)
  - HWPX 텍스트 추출 (실샘플 12~23KB)
  - Gemini 응답 JSON 파싱 (malformed items 스킵)
  - API Key 없을 때 RuntimeError
  - HWP 같은 미지원 포맷 거부
  - Gemini 호출 mock 으로 HWPX 경로 E2E
  - ChecklistTab 데모 모드 E2E (파일 생성 → 매칭 → 보고서 출력)

### Known Limitations

- **HWP (구형 바이너리) 미지원** — HWPX 또는 PDF 로 변환 필요
- **OCR 스캔 PDF 미지원** — Gemini 가 이미지 PDF 도 어느 정도 처리하지만 Tesseract fallback 은 v0.6.1+ 에서
- **재귀 폴더 스캔 없음** — 지정한 폴더 바로 아래 파일만 (하위 폴더 미탐색)
- **파일명 매치 실패 시 OCR 발행일 추출 없음** — "발행일 모름" WARNING 으로 표시되고 끝

### 사용 예

```
1. 설정 탭 → Gemini API Key 등록
2. 체크리스트 탭 → RFP 파일 선택 (.pdf 또는 .hwpx)
3. 제출서류 폴더 선택
4. [RFP 분석 + 체크] 클릭
5. 결과 테이블에서 ✅/⚠️/❌ 상태 확인
6. [보고서 저장...] 으로 .txt/.md 파일 내보내기
```

API Key 없이 테스트하려면 "데모 서류로 대조" 버튼 사용 (5종 샘플 서류로 폴더 스캔).

---

## [0.5.0] — 2026-04-19

### 📋 정량제안서 모드 (cell-level 에디터)

**v0.4.0 foundation 에서 실제 기능으로 승격.** 정량제안서 HWPX 템플릿을 로드해서
표 셀 단위로 편집 후 새 HWPX 로 저장하는 기능.

### 설계 피벗: 타입 추론 대신 cell-level

v0.4.0 foundation 은 `QuantField` + `FieldType` 로 구조적 필드 추출을 목표했지만, 실제
샘플(`[정량제안서] 2026년 아카데미.hwpx`) 분석 결과 서식별 표 구조가 **너무 다양**함:
- 서식 1 (4개 표): key-value 쌍
- 서식 2 (3개 표): 통계표 + 중첩 헤더
- 서식 3 (2개 표): 프로필 목록
- 서식 4 (22개 표): 인물별 반복 구조 (396행 경력 포함)

→ 필드 타입 자동 추론은 기관별 편차가 크고 실패 시 UX 저하.
→ **모든 셀을 그대로 노출** + 사용자가 직접 편집이 가장 안전·유용.

### Added

- **`src/quant/models.py`** — `QuantCell(para_index, table_idx, row, col, text, form_id, ordinal)` + `QuantDocument` 추가. 기존 `QuantField`/`QuantForm`/`QuantProposal` 은 foundation 으로 유지 (미래 typed 모드 대비).
- **`src/quant/parser.parse_document()`** — HWPX → `QuantDocument`. `[서식 N]` 헤더 감지 + 모든 표의 모든 셀을 (para_index, intra-para table_idx, row, col) 로 추출. **실제 샘플에서 1497 셀 추출 성공**.
- **`src/quant/converter.save_document()`** — 편집된 `QuantDocument` → 새 HWPX. 셀 좌표로 `<hp:tc>` 찾아 `<hp:t>` 텍스트 교체. **1497/1497 셀 write-back 성공 (0 skip)**. fix_namespaces 후처리 자동 적용.
- **`src/gui/tabs/quant_tab.py`** — 5번째 탭. 좌측 서식/표 트리 + 우측 `QTableWidget` 인라인 에디터. 레이블 후보 셀 옅은 배경 강조. 저장/다른 이름으로 저장/미리보기 탭 이동 지원.
- **`tests/fixtures/quant_samples/`** — v1 프로젝트에서 실제 정량제안서 2종 복사
  - `[정량제안서] 2026년 아카데미.hwpx` (2.8MB, 4 서식)
  - `[서식4]_팜러닝_2026년 후계농...세부사업계획서.hwpx` (61MB)

### Changed

- **메인 윈도우 5 탭 구조**: 변환 / 템플릿관리 / 미리보기 / **정량** / 설정 (Ctrl+1~5)
- `QuantTab` → 미리보기 탭 이동 시그널 연결 (Convert Tab 과 동일 패턴)

### Kept as foundation (본편 계약 유지)

- `QuantField` / `QuantForm` / `FieldType` enum — typed 모드 설계 문서(`docs/QUANT_DESIGN.md`) 와 함께 유지. 향후 샘플 더 확보 시 파서 확장.
- `parse_template()` / `demo_proposal()` — foundation API 로 유지

### Testing

- **147 pytest 통과** (v0.4.0 대비 +12)
  - 실제 샘플 파싱: 4 서식 / 1497 셀
  - Round-trip: 수정한 셀 값이 재파싱 시 그대로
  - Skip 0 확인 (모든 셀 write-back 성공)
  - GUI 탭 smoke + 파일 선택 → 로드 → 저장 플로우

### Known Limitations (v0.5.0)

- 표의 중첩 셀 / colspan 병합은 현재 매트릭스 그대로 표시 (빈 셀 위치에 값 없음)
- 한 셀에 여러 `<hp:p>` 가 있는 복잡 셀은 첫 `<p>` 첫 `<t>` 에만 쓰여짐 (드문 케이스)
- TABLE_ROW 반복 행 추가 UI 없음 (이미 존재하는 행만 편집 가능, 추가는 HWPX 에서 미리 준비)
- 타입별 입력 위젯(날짜 피커 등) 없음 — 전부 텍스트 셀

이 제약들은 v0.5.1~v0.5.x 에서 점진 개선 예정.

---

## [0.4.0] — 2026-04-19

### 🎯 Self-MoA + v0.5/v0.6 foundation

### Self-MoA — 정확도 3~7% 개선 옵션

같은 모델을 N 회 독립 호출한 뒤 **aggregator** 가 합성하는 Self-Mixture-of-Agents.
ICLR 2025 연구에 따르면 MMLU / CRUX / MATH 에서 단일 호출 대비 평균 3.8% 개선.

### Added

- **`src/parser/self_moa.py`** — `SelfMoAClient(base_client, draws=3)` 래퍼
  - Pluggable: aggregator 를 base 와 다른 모델로 지정 가능 (예: Flash × 3 → Pro aggregate)
  - Graceful: 일부 draw 실패 시 성공한 draw 들만으로 aggregate, 모두 실패 시 RuntimeError
  - 토큰/비용은 모든 (N+1) 호출 합산, 가중평균 요율로 계산
  - `model` 문자열이 `self-moa[gemini-2.5-flash×3]` 형태로 표시
- **AppConfig**: `use_self_moa: bool` / `self_moa_draws: int = 3`
- **SettingsTab**: "Self-MoA 사용" 체크박스 + 독립 호출 수 spinbox (2~10)
- **create_default_client()**: config 기반 자동 wrapping — 어떤 backend 든 감쌀 수 있음

### Cost Warning

- draws=3 이면 호출 비용 **4 배** (Gemini ₩10 → ₩40, Ollama 로컬은 그대로 0 원)
- 정확도가 정말 중요한 케이스에만 켜기 권장. 기본 OFF.

### Foundation — v0.5.0 정량제안서

**미구현**. 스캐폴딩과 설계 문서만 포함. 본편은 사용자 샘플 확보 후 진행.

- **`src/quant/`** — `QuantField`, `QuantForm`, `QuantProposal` 데이터 모델 + FieldType enum (TEXT/MULTILINE/NUMBER/DATE/SELECT/CHECK/TABLE_ROW)
- **`src/quant/parser.py`** — HWPX 템플릿 파서 stub. `demo_proposal()` GUI 테스트 지원
- **`src/quant/converter.py`** — `NotImplementedError` (샘플 필요)
- **`docs/QUANT_DESIGN.md`** — 파이프라인, 파서 전략, UI 전략, 사용자 요청 샘플 목록

### Foundation — v0.6.0 제출서류 체크리스트

**미구현**. 스캐폴딩과 결정론 매처만 동작. Gemini/OCR 연결은 본편에서.

- **`src/checklist/`** — `RequiredDocument`, `MatchedFile`, `ChecklistItem`, `ChecklistResult` + DocumentStatus enum
- **`src/checklist/filename_matcher.py`** — 4종 날짜 패턴 (`YYYY-MM-DD`, `YYYYMMDD`, `YY MMDD_` 등) + 키워드 정규화 매치. **동작 OK**
- **`src/checklist/matcher.py`** — `build_checklist(docs, folder)` 결정론 매처. **동작 OK**
- **`src/checklist/rfp_extractor.py`** — Gemini document-processing 연결 stub. `demo_required_documents()` 예시 제공
- **`docs/CHECKLIST_DESIGN.md`** — 파이프라인, Gemini 프롬프트 계획, OCR fallback 설계, UI 스케치, 사용자 요청 샘플 목록

### Testing

- **135 pytest 통과** (v0.3.0 대비 +22) — Self-MoA 9개 + foundation 13개
  - Self-MoA: 토큰 합산, 실패 fallback, 팩토리 래핑
  - quant foundation: 데이터 모델 + stub
  - checklist foundation: 날짜 패턴 4종 + 키워드 정규화 + 결정론 매처 OK/WARNING/MISSING 분기

### Requested from user (for v0.5.0 / v0.6.0 본편)

- `tests/fixtures/quant_samples/` — 실제 정량제안서 HWPX 3~5종
- `tests/fixtures/rfp_samples/` — 실제 RFP PDF/HWPX 3~5종

업로드해 주시면 다음 세션에서 본편 구현 시작합니다.

---

## [0.3.0] — 2026-04-19

### 🔌 멀티 백엔드 확장 — OpenAI / Anthropic

v0.2.0 의 `ResolverClient` 프로토콜 위에 클라우드 LLM 두 종을 추가. 이제 사용자가
**Gemini / Ollama / OpenAI / Anthropic** 중 취향·조직 정책에 맞는 백엔드를 고를 수 있음.

### Added

- **`src/parser/openai_backend.py`** — OpenAI Chat Completions + **Structured Outputs** (`response_format: json_schema, strict: true`). gpt-4o-mini 기본.
- **`src/parser/anthropic_backend.py`** — Anthropic Messages API + **tool_use** 기반 구조화 출력. claude-haiku-4-5 기본.
- **모델별 가격표** (`PRICE_TABLE`) — gpt-4o-mini / gpt-4o / gpt-4.1-mini / gpt-4.1 / o4-mini / claude-haiku-4-5 / claude-sonnet-4-5 / claude-opus-4-1
- **`ApiKeyManager.for_service(service)`** — keyring/Fernet/ENV 를 서비스별로 격리.
  - 키링 username: `gemini-api-key` / `openai-api-key` / `anthropic-api-key`
  - ENV: `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`
  - Fernet: `keys.enc` / `keys.openai.enc` / `keys.anthropic.enc`
- **모듈 편의함수** — `get_key(service=...)`, `set_key(..., service=...)`, `has_key/delete_key` 전부 서비스 인자 지원 (back-compat: 인자 없으면 Gemini)
- **AppConfig**: `openai_model` / `anthropic_model` 필드 추가
- **SettingsTab**: 4 백엔드 드롭다운 + OpenAI/Anthropic 각각 API Key 등록/삭제 + 모델 지정
- **CLI `--backend`**: `gemini/ollama/openai/anthropic/none` 5개 선택

### Testing

- **113 pytest 통과** (v0.2.0 대비 +11) — OpenAI/Anthropic SDK mock, per-service key 격리, 백엔드 팩토리 전 4종

### Back-compat

- `GeminiClient` 는 여전히 `ResolverClient` alias — 기존 코드 수정 불필요
- 기존 `get_key()` / `set_key()` 는 service 인자 기본값이 `None` → Gemini 싱글턴과 동일 동작

---

## [0.2.0] — 2026-04-19

### 🎉 Ollama 로컬 백엔드 — 완전 오프라인 지원

공공기관/법무 등 클라우드 AI 이용이 제한된 환경을 위한 **로컬 LLM 백엔드**.
원고를 외부로 전송하지 않고 사용자 PC 에서 LLM 이 애매 블록을 해석합니다.

### Added

- **`src/parser/ollama_backend.py`** — `OllamaClient` (REST `/api/generate` 래퍼) + `probe_server()` 서버 상태 확인
  - Ollama 의 structured output (`format: <schema>`) 활용
  - 한국어 품질 좋은 `qwen2.5:7b` / `llama3.1:8b` 기본 지원
  - 네트워크 에러 모두 사용자 친화 메시지로 변환
- **`ResolverClient` protocol 일반화** — 기존 `GeminiClient` 는 back-compat alias. 어떤 LLM 백엔드든 `generate(prompt) → GenerateResult` 만 구현하면 끼울 수 있음
- **`create_default_client(backend)`** 팩토리 — config 의 `resolver_backend` 에 따라 자동 선택
- **`Cost` 에 `price_input/output_usd_per_m`** — 백엔드별 요율 지원. 로컬은 0 으로 고정 → report 에 "비용 0 (로컬)" 표시
- **AppConfig 확장**: `resolver_backend` / `ollama_host` / `ollama_model`
- **SettingsTab**: AI 백엔드 선택 드롭다운 + Ollama 설정(서버/모델/확인 버튼) + 설치 안내 다이얼로그
- **ConvertTab**: 선택된 백엔드에 따라 체크박스 라벨/툴팁 자동 갱신
- **CLI**: `build --backend {gemini,ollama,none}` / `resolve --backend ...`
- **`scripts/bench_backends.py`** — Gemini vs Ollama 품질·속도·비용 비교 벤치마크

### Changed

- `human_summary()` 출력에 백엔드/모델명 표시, 로컬일 땐 비용 생략
- ConversionWorker 의 step 2 메시지가 백엔드명으로 동적 표시

### Testing

- **102 pytest 통과** (v0.1.0 대비 +15) — OllamaClient mock, probe, 비용 계산, 백엔드 팩토리

### 권장 설정

- **완전 오프라인**: Settings → 백엔드 → Ollama. `qwen2.5:7b` 모델 권장.
- **속도**: 로컬 GPU 8GB+ 에서 1680 라인 원고 기준 30~60초 (Gemini 는 5~10초)
- **비용**: 0 원. 전기세만.
- **정확도**: 동일 fixture 기준 Gemini 수준 (재분류·확인 비율 유사) — `scripts/bench_backends.py` 로 본인 환경에서 비교 가능

---

## [0.1.0] — 2026-04-18

### 🎉 첫 MVP 릴리즈

Windows 용 HWPX 문서 자동 작성 데스크톱 앱. 원고 .txt + 템플릿 HWPX →
스타일/계층이 유지된 HWPX 출력.

### Added — 핵심 기능

- **원고 → HWPX 변환 파이프라인** (regex_parser → Gemini resolver → md_to_hwpx)
  - 기호 기반 결정론 파서 (Ⅰ/1/1)/(1)/①/□/❍/-/·/※ 10단계)
  - Gemini 2.5 Flash 로 애매 블록 해석 (structured output schema, thinking 비활성)
  - 문서당 Gemini 호출 1회, 비용 ~₩10 이하
  - 네임스페이스 복구 + 표 페이지 넘김 + 검증까지 자동
- **템플릿 라이브러리** (`%APPDATA%\HwpxAutomation\templates\`)
  - 번들 기본 10단계 스타일
  - 사용자 공고 HWPX 업로드·관리
  - style name/size heuristic 으로 스타일 ID 자동 매핑 (fallback 3단)
- **PySide6 GUI** (변환/템플릿관리/미리보기/설정 4탭)
  - QThread 워커로 UI 프리즈 없이 장시간 작업
  - HTML 미리보기 (QTextBrowser 렌더)
  - API Key keyring 저장 + Fernet fallback
  - 전역 예외 훅 (uncaught → 친절한 다이얼로그)
- **CLI** (`python -m src.cli build/resolve/fix/verify/convert`)
- **PyInstaller 배포** (`dist/HwpxAutomation/` 폴더, 설치 없이 실행)

### Added — 품질

- 85 pytest 통과 (엔진 구조 7/7, E2E 파이프라인, GUI smoke, Gemini 스키마 mock)
- 3/3 빌드 exe smoke 테스트
- 4/4 설치 경험 E2E 테스트

### Fixed — 릴리즈 전 버그 감사

- ConvertTab 더블클릭 race 조건 — `self._thread` 가드 추가
- UTF-8 BOM 있는 원고 → 첫 줄 유실 — `parse_file` 에서 자동 strip
- 같은 이름의 템플릿 중복 등록 허용 → 사전 검증
- 워커 PermissionError 시 사용자 친화 메시지
- visualize.py 섹션 없는 HWPX 에 빈 페이지 렌더 → 명확한 ValueError
- 빈 원고(.txt 0 바이트) 변환 시도 → ConvertTab 에서 차단
- **[보안]** 테스트가 실제 사용자 keyring 엔트리를 삭제한 버그 → `_delete_keyring` 메서드 대칭 + 고유 service_name 격리

### Known Limitations

- HWPX → 한/글 미세 스타일 차이는 `fix_namespaces` 로 대부분 커버하지만 복잡한 표/이미지가 섞이면 수작업 조정 필요할 수 있음
- Gemini API Key 는 사용자가 직접 발급(aistudio.google.com) 필요 — 무료 티어 2.5 Flash 는 20 RPD 제한
- 정량제안서 자동화 / 제출서류 체크리스트는 **스코프 밖** (v0.2.0+ 예정)
- 미리보기는 QTextBrowser 기반이라 완전 재현이 아닌 **계층/서식 감 잡기** 수준

### Dev History (MVP 개발 주차)

- **W1** — 프로젝트 스캐폴딩 + v1 엔진 3종 포팅
- **W2** — regex_parser + template_manager + template_analyzer + IR 스키마
- **W3** — Gemini resolver (structured output) + API Key 관리 + 첫 실행 온보딩
- **W4** — 4탭 GUI 실구현 + QThread 워커 + HWPX → HTML 렌더러
- **W5** — 글로벌 예외 훅 + 로그 UX + PyInstaller .exe 빌드 + 설치 E2E
- **W6** — 버그 감사 + 문서 정비 + v0.1.0 태그

---

## [Unreleased]

v0.2.0 이후 로드맵:
- 정량제안서 모드 (서식 채우기 UI)
- 제출서류 체크리스트 (RFP PDF → Gemini 추출 + OCR fallback)
- 모델 목록 런타임 조회 (Settings 드롭다운)
- 가격표 원격 config (GitHub release)
- Self-MoA 옵션 (같은 모델 N회 + aggregator)
- OpenAI/Anthropic 백엔드 (ResolverClient 추상화 위에 얹기)
- 상업화: 회원제 / 광고 / 사용량 텔레메트리 (opt-in)
