---
name: release-flow
description: HWPX Automation v2 릴리즈 작업 전체 흐름. 버전 범프 → 빌드 → 릴리즈 zip → 검증.
---

# 릴리즈 플로우 스킬

단일 개발자가 혼자 돌려야 하는 릴리즈 절차. 각 단계는 다음 단계를 막으면 안 된다.

## 체크리스트

```bash
# 1) 코드 준비
cd "D:\03_antigravity\25_hwpx automation v2"
# 전체 테스트 (반드시 올 그린)
python -m pytest -q

# 2) 버전 범프 (2 곳 동기)
# src/__init__.py::__version__
# pyproject.toml::version

# 3) CHANGELOG.md 작성
#   ## [0.X.Y] — YYYY-MM-DD
#   ### 개선 / 수정 / 추가 사항

# 4) 빌드 (onedir 권장)
rm -rf build/ dist/HwpxAutomation
python -m PyInstaller build.spec --noconfirm

# 5) 릴리즈 zip 생성
python scripts/make_release.py
# → release/HwpxAutomation-v0.X.Y.zip (≈ 411 MB)

# 6) 수신자 시뮬 검증
python scripts/verify_release_zip.py
# → exe 기동 8 초 + 번들 템플릿 확인

# 7) 체크 (선택): git tag
# git tag v0.X.Y
# git push --tags
```

## 버전 번호 정책 (SemVer)

- **MAJOR** (1.0.0) — 비호환 API 변경
- **MINOR** (0.X.0) — 기능 추가 (하위 호환)
- **PATCH** (0.X.Y) — 버그 수정 / 품질 개선

예시:
- v0.10.0 → v0.10.1 : HWP BodyText 노이즈 필터 (품질)
- v0.10.1 → v0.11.0 : AI 기본법 대응 추가 (기능)
- v0.11.0 → v1.0.0 : 상용 정식 릴리즈 / 코드사이닝 / GS 인증

## PyInstaller 상식

- `--clean` 은 Windows 에서 종종 `PermissionError` → `rm -rf build/` 수동 후 재시도
- `launcher.py` 는 **절대 import** 를 쓰므로 직접 `src/main.py` 를 entry 로 잡지 말 것
- 신규 서브패키지 추가 시 `build.spec` 의 `collect_submodules("src.<new>")` 확인

## `PYTHONIOENCODING=utf-8`

**v0.10.1 부터 불필요** — `scripts/*.py` 최상단에서 `sys.stdout.reconfigure("utf-8")` 자동 실행.

## 릴리즈 노트 포맷

CHANGELOG 의 해당 버전 섹션을 `print_release_notes()` 가 자동 추출 → 콘솔 출력.

## 실패 시 복구

| 증상 | 해결 |
|---|---|
| `BadZipFile: File is not a zip file` | 템플릿 dummy 파일로 테스트한 흔적 — 실템플릿으로 교체 |
| `PermissionError: build/build/localpycs` | `taskkill /F /IM HwpxAutomation.exe` 후 재빌드 |
| exe 기동 8 초 미만 조기 종료 | `verify_release_zip.py` 가 stderr 보여줌 — collect_submodules 누락 가능 |
| 이모지 cp949 에러 | `PYTHONIOENCODING=utf-8` 환경변수 (v0.10.0 이하만) |

## 상용 전환 체크포인트 (v1.0 준비)

- [ ] 코드사이닝 (OV 인증서, 연 10~20만원)
- [ ] SmartScreen 평판 축적 (초기 배포 볼륨 확보)
- [ ] GS 인증 (공공조달 등재)
- [ ] AI 기본법 UI 고지 (v0.11.0 완료)
- [ ] 오픈소스 고지 페이지
- [ ] 개인정보 동의 (PIPA, Pro 클라우드 AI 경로)
