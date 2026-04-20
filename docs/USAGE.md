# HWPX Automation — 사용자 매뉴얼

v0.1.0 기준. GUI / CLI 양쪽 모두 커버합니다.

---

## 1. 설치

### 옵션 A. 빌드된 배포본 (설치 불필요)

1. 배포자에게서 받은 `HwpxAutomation` 폴더(약 210MB) 를 원하는 위치로 복사
2. 폴더 안 `HwpxAutomation.exe` 더블클릭
3. 끝

Windows 10 이상에서 동작. 관리자 권한 필요 없음.

### 옵션 B. 소스에서 직접 실행 (개발자)

```powershell
git clone <repo>  # 또는 폴더 복사
cd "hwpx automation v2"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.main
```

Python 3.11 이상 필요.

---

## 2. 첫 실행 — API Key 등록

앱이 뜨자마자 **"Gemini API Key 설정"** 다이얼로그가 나옵니다.

### Key 발급 방법

1. https://aistudio.google.com/apikey 접속
2. Google 계정으로 로그인
3. **Create API key** → **Create API key in new project**
4. 생성된 `AIza...` 시작 문자열 전체 복사

### Key 등록

1. 다이얼로그의 입력 필드에 붙여넣기
2. **[ ] 키 표시** 체크박스로 키가 제대로 복사됐는지 확인 가능
3. **연결 테스트** 클릭 → "✅ 연결 성공 model count=N" 메시지 확인
4. **저장** 클릭

Key 는 **Windows 자격 증명 관리자(Credential Manager)** 에 암호화 저장됩니다. 평문 저장 안 됨.

### Key 없이 사용하기

**건너뛰기 (Gemini 비활성)** 버튼 클릭. 이 경우:
- 결정론 파서만 동작 (90%+ 정확도)
- 애매한 계층은 사용자가 원고에서 미리 기호를 명확히 찍어야 함
- 비용 0원

이후 언제든 **설정 탭** 에서 Key 등록 가능.

---

## 3. 변환 탭

### 흐름

```
템플릿 드롭다운에서 선택
      ↓
파일 선택 버튼으로 원고 .txt 업로드
      ↓
(선택) Gemini 해석 사용 체크
      ↓
변환 실행
      ↓
진행 로그 (실시간)
      ↓
✅ 변환 완료
      ↓
다른 이름으로 저장 / 미리보기 탭으로
```

### 진행 로그 이해하기

```
[1/5] 원고 분석 중...
  → 1169 블록, 애매 226
[2/5] Gemini 해석 중 (애매 226개)...
  → 애매 블록 226 개 / 재분류 5 / 확인 219 / 응답누락 2 / 파싱실패 0 · 호출 1 회 · 비용 ≈ $0.0069 (₩9.3) · tokens in=54355 out=9416 think=0
[3/5] 템플릿 스타일 분석 중...
  ℹ️ 일부 레벨 fallback 사용: [5]
[4/5] HWPX 생성 중...
  → output.hwpx 생성 완료
[5/5] 결과 HWPX 검증 중...
  → 구조 검증 7/7 통과 (전체 8/11, 73%)

✅ 변환 완료
```

| 숫자 | 의미 |
|---|---|
| 블록 | regex 가 추출한 IR 요소 개수 |
| 애매 | Gemini 해석을 요청받은 블록 |
| 재분류 | Gemini 가 원래 레벨과 다르게 판정한 개수 (regex 버그 신호) |
| 확인 | Gemini 가 원래 레벨이 맞다고 확인한 개수 |
| 응답누락 | 응답 배열에 포함 안 된 블록 (truncation 등) |
| 구조 검증 | 파일 형식/네임스페이스/표 등 7개 체크 |

### 출력 파일 경로

기본: `%USERPROFILE%\Documents\HwpxAutomation\<원고이름>_<타임스탬프>.hwpx`

**설정 탭**에서 변경 가능.

### 실패 시 로그 저장

변환 실패 시 "진행 로그를 파일로 저장하시겠습니까?" 다이얼로그가 뜹니다. 버그 리포트 첨부용으로 유용.

---

## 4. 템플릿 관리 탭

### 구조

- **좌측**: 등록된 템플릿 목록 (★ = 기본)
- **우측**: 선택된 템플릿의 상세 정보 (경로, 등록일, 폰트/페이지 설정, 스타일 매핑)

### 공고 양식 업로드

1. **+ 추가** 클릭
2. `.hwpx` 파일 선택
3. 라이브러리 이름 입력 (중복 금지)
4. 등록 완료 → 변환 탭 드롭다운에 자동 반영

### 설정 상세 읽기

선택한 템플릿의 우측 패널:

```
이름:       농정원 2026 공고양식
ID:         user_a3f8b12d
파일:       농정원_2026.hwpx
기본:       아니오
등록일:     2026-04-18
경로:       C:\Users\...\AppData\Roaming\HwpxAutomation\templates\...

─ 스타일 매핑 (analyzer 결과) ─
페이지: A4 여백 15/15/20/20mm
  레벨  1: 제목1              20.0pt
  레벨  2: 제목2              18.0pt
  레벨  3: 제목3              18.0pt
  레벨  4: 본문1              16.0pt
  레벨  5: (fallback)         15.0pt
  레벨  6: □ 4칸              15.0pt
  ...
  ⚠️ fallback: [5]
```

**⚠️ fallback** 이 있으면 해당 레벨에서 번들 기본 스타일이 쓰인다는 뜻. 공고 양식에 해당 스타일이 명시 이름으로 정의돼 있지 않을 때 나타남.

### 삭제

선택 후 **- 삭제** 클릭. 확인 후 파일까지 제거됩니다.

**기본 템플릿(★ 기본 10단계 스타일)** 은 삭제 불가 (안전 장치).

### 기본 변경

사용자 템플릿 중 하나를 선택 후 **★ 기본으로** 클릭 → 변환 탭 드롭다운의 첫 번째로 이동.

---

## 5. 미리보기 탭

생성된 HWPX 를 HTML 로 렌더해서 **계층/서식 감** 잡기 용.

### 기능

- **파일 열기...** — 임의의 HWPX 로드
- **새로고침** — 파일이 변경됐을 때 재렌더
- **한/글로 열기** — 한/글이 설치된 경우 기본 연결 프로그램 실행

### 제약

QTextBrowser 기반이라 **완전 재현 아님**:
- 표는 HTML `<table>` 로 표시 (셀 병합 일부 손실 가능)
- 이미지는 표시되지 않음
- 페이지 나눔 미반영 (연속 스크롤)
- 복잡한 서식(그림자, 투명도 등) 미반영

정확한 모양은 **한/글로 열기** 로 확인하세요.

---

## 6. 설정 탭

### AI 백엔드 (v0.2.0~)

- **백엔드 드롭다운** — `Gemini` / `Ollama` / `사용 안 함` 중 택일
- 선택한 백엔드에 맞게 하위 옵션 그룹이 강조됨

### Gemini API

- **API Key 변경/등록** — 다이얼로그 재실행
- **연결 테스트** — 저장된 키로 Gemini 서버 ping (사용 가능 모델 수 표시)
- **API Key 삭제** — keyring 과 Fernet 파일 모두 제거

### Ollama (로컬)

- **서버 URL** — 기본 `http://localhost:11434` (다른 PC 에서 돌릴 때만 변경)
- **모델** — 기본 `qwen2.5:7b`. 추천: `qwen2.5:7b`(한국어 좋음, 8GB VRAM), `llama3.1:8b`(가벼움, 4~8GB), `qwen2.5:14b`(정확도 높음, 16GB), `qwen2.5:3b`(CPU 전용)
- **서버 확인** — Ollama 가 실행 중인지 + 설치된 모델 목록 확인
- **Ollama 설치 안내** — 다운로드 링크 + `ollama pull` 명령어

### Gemini 해석 옵션

- **Gemini 해석 사용** (체크박스) — 변환 시 애매 블록 Gemini 호출
- **모델** — 기본 `gemini-2.5-flash`. 다른 모델(`gemini-2.5-pro` 등) 지정 가능
- **일일 호출 한도** — 안전 상한 (무료 티어 보호)
- **애매 기준 길이** — 기호 붙은 본문이 이 길이 이상이면 Gemini 해석 요청
  - **낮출수록**: 정확도 ↑ / 비용 ↑
  - **높일수록**: 비용 ↓ / 정확도 ↓
  - 기본 **50**. 대부분 50~80 권장

### 저장 경로

변환 결과 HWPX 기본 경로. `찾아보기...` 로 변경.

### 로그 / 유틸

- **로그 레벨** — DEBUG / INFO / WARNING / ERROR
- **로그 폴더 열기** — `%APPDATA%/HwpxAutomation/logs/` 탐색기
- **템플릿 폴더 열기** — `%APPDATA%/HwpxAutomation/templates/`
- **앱 데이터 폴더 열기** — `%APPDATA%/HwpxAutomation/`

### 저장

변경사항은 **설정 저장** 버튼으로 확정. 버튼은 변경 있을 때만 활성화.

---

## 7. CLI 레퍼런스

GUI 가 부담스럽거나 배치 처리할 때.

### build — 풀 파이프라인 (권장)

```powershell
python -m src.cli build `
    --template templates/00_기본_10단계스타일.hwpx `
    --txt my_proposal.txt `
    --output out.hwpx `
    --use-gemini `
    --backend ollama `
    --verify
```

| 옵션 | 설명 |
|---|---|
| `--template` | 필수. HWPX 템플릿 경로 |
| `--txt` | 필수. 원고 .txt |
| `--output` | 필수. 결과 HWPX 경로 |
| `--use-gemini` | LLM 해석 활성 (backend 에 따라 Gemini 또는 Ollama) |
| `--backend` | `gemini` / `ollama` / `none` — 명시 override (기본은 config) |
| `--verify` | 변환 후 검증 리포트 출력 |
| `--type` | `qualitative` / `quantitative` / `auto` (verify 용) |
| `--no-fix-namespaces` | 네임스페이스 후처리 비활성 (디버깅) |

### resolve — 애매 블록만 Gemini 해석

```powershell
python -m src.cli resolve my_proposal.txt --dry-run
```

`--dry-run` 이면 IR 수정 없이 비용/품질만 리포트.

### fix — 네임스페이스 후처리만

```powershell
python -m src.cli fix broken.hwpx --fix-tables
```

v1 엔진이 아닌 외부에서 만든 HWPX 보정용.

### verify — 검증만

```powershell
python -m src.cli verify output.hwpx --type qualitative
```

구조 + 내용 체크 리포트 출력. 종료 코드 0 = 70%+ 통과, 1 = 실패.

### convert — v1 호환 (마크다운 입력)

```powershell
python -m src.cli convert --template T.hwpx --md content.md --output out.hwpx
```

`.md` (마크다운) 을 그대로 변환하는 v1 경로. 대부분은 `build` 권장.

---

## 8. 자주 묻는 질문

### Q. 한/글이 설치돼 있어야 하나요?
**아니오.** 변환 자체는 순수 XML 조작으로 `HwpxAutomation.exe` 만으로 됩니다. **미리보기** 의 "한/글로 열기" 만 한/글이 필요합니다.

### Q. Gemini API 키가 꼭 필요한가요?
**아니오.** 첫 실행에서 "건너뛰기" 선택 시 결정론 파서만으로 동작합니다. 원고에 기호를 명확히 찍을 수 있다면 충분. 애매한 경우가 많은 큰 문서에는 Gemini 쓰는 게 유리.

### Q. 회사 정보가 Google 에 전송되나요?
- **Gemini 해석 사용 시**: 원고의 **애매한 줄 + 주변 3줄 문맥**이 Gemini API 로 전송됨. 나머지 본문은 로컬에서만 처리.
- **Gemini 건너뛰기**: 네트워크 전송 없음, 완전 로컬.
- 민감 정보가 많다면 Gemini 비활성 권장. Ollama 로컬 백엔드는 v0.2.0+ 로드맵.

### Q. 비용이 얼마나 나오나요?
평균 제안서 1건 기준 **~₩10** (Gemini 2.5 Flash 무료 티어는 20 요청/일까지 무료). 하루 수십 건 돌려도 무료 안에서 해결됩니다.

### Q. 결과가 한/글에서 이상하게 보여요
1. **미리보기 탭**에서 먼저 HTML 렌더 확인
2. 이상 있으면 **진행 로그 저장** → 지원 채널에 제보
3. 한/글에서만 이상하면 **설정 → 로그 폴더 열기** 에서 최근 로그 확인
4. verify 의 ⚠️ 항목 중 "Namespace Pollution" / "Table treatAsChar" 가 실패면 `fix_namespaces --fix-tables` 재시도

### Q. 배포할 수 있나요?
**네**, MIT 라이선스입니다. 단 의존성(PySide6 LGPL) 은 동적 링크 형태 유지 필요 (PyInstaller `--onedir` 이 자동 충족).

---

## 9. 알려진 한계

- 미리보기는 완전 재현 아님 (QTextBrowser 제약)
- Gemini 무료 티어 20 RPD 를 넘으면 하루 대기 필요
- 복잡한 표(셀 병합/다중 중첩) 는 수작업 조정 필요할 수 있음
- HWP (.hwp, 구형 바이너리) 는 미지원 — HWPX 만
- 정량제안서 [서식 N] 자동 채우기는 v0.2.0+ 예정

---

## 10. 문제 신고

- **버그**: GitHub Issues (가능하면 진행 로그 파일 첨부)
- **질문**: Discussions
- **보안 이슈**: 비공개 채널 (LICENSE 참고)
