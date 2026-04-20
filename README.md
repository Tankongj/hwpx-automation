# HWPX Automation v0.2.0

한글(HWPX) 문서를 AI 보조로 자동 작성하는 **Windows 데스크톱 앱**.

원고 `.txt` + 템플릿 `.hwpx` → 스타일/계층이 유지된 완성본 `.hwpx`.

공무원·행정사·법무사·변호사 등 한글 문서를 반복 작성하는 사무직 전문가군을 위해 설계됐습니다.

---

## 5분 가이드 (처음 사용)

### 1. 앱 실행

이미 빌드된 배포본이 있다면:

```
dist\HwpxAutomation\HwpxAutomation.exe
```

폴더 전체(210MB 정도) 를 원하는 위치로 복사해서 쓰시면 됩니다. 설치 불필요.

소스에서 직접 실행하려면:

```powershell
pip install -r requirements.txt
python -m src.main
```

### 2. AI 백엔드 선택 (v0.2.0~)

앱에는 3가지 AI 백엔드가 있습니다. 환경에 맞게 선택:

| 백엔드 | 비용 | 정확도 | 프라이버시 | 언제 쓰나 |
|---|---|---|---|---|
| **Gemini** (기본) | ~₩10/문서 | 🟢 높음 | 🔴 Google 서버로 전송 | 일반 사용 |
| **Ollama** (로컬) | **0 원** | 🟢 높음 | 🟢 **완전 오프라인** | 공공기관 / 법무 / 민감 정보 |
| **사용 안 함** | 0 원 | 🟡 결정론만 | 🟢 네트워크 없음 | 기호 명확한 원고 |

앱이 뜨자마자 **API Key 입력 다이얼로그**가 나옵니다 (Gemini 경로):

1. [Google AI Studio](https://aistudio.google.com/apikey) 로그인 → "Create API key" 클릭
2. 생성된 `AIza...` 문자열 복사 → 다이얼로그에 붙여넣기
3. **연결 테스트** → "사용 가능 모델 N개" → **저장**

키는 Windows 자격 증명 관리자(keyring)에 암호화 저장됩니다.

**Ollama 로컬 백엔드 사용하려면** (선택):

1. https://ollama.com/download 에서 Windows 설치
2. `ollama pull qwen2.5:7b` (또는 다른 모델)
3. 앱의 **설정 탭** → AI 백엔드 → **Ollama** 선택 → **서버 확인** → 저장

이후 변환은 원고 한 자도 외부로 전송되지 않습니다.

### 3. 원고 준비

원고 `.txt` 파일의 계층 기호 예시:

```
# 2026년 귀농귀촌 아카데미 운영 제안서  ← 표지 제목

# Ⅰ. 기관현황                              ← 1단계 (장)

## 1. 일반현황                             ← 2단계 (절)

### 1) 제안사 현황                          ← 3단계 (소절)

(1) 기관 개요                              ← 4단계

① 주요 사업                                 ← 5단계

□ 교육 운영                                ← 6단계 (대주제)

❍ 온라인 콘텐츠 제작                        ← 7단계 (중주제)

- 연간 50편 이상                           ← 8단계 (하이픈)

· 평균 15분 분량                           ← 9단계 (가운뎃점)

※ 자세한 내용은 부록 참조                   ← 10단계 (주석)
```

### 4. 변환 실행

1. **변환 탭** 선택
2. 템플릿 드롭다운에서 "기본 10단계 스타일" 확인
3. **원고: 파일 선택...** → 방금 만든 `.txt`
4. **변환 실행** 클릭

5~10초 뒤 진행 로그에 `✅ 변환 완료` 출력.

### 5. 결과 확인 & 저장

- **미리보기 탭으로** — HTML 렌더로 계층/서식 확인
- **다른 이름으로 저장...** — 원하는 경로로 사본 복사
- **한/글로 열기** — 한/글이 설치돼 있으면 바로 실행

기본 저장 경로: `%USERPROFILE%\Documents\HwpxAutomation\`

---

## 주요 기능

| 탭 | 기능 |
|---|---|
| **변환** | 템플릿 선택 → 원고 업로드 → 변환 실행 → 실시간 로그 |
| **템플릿 관리** | 번들 기본 템플릿 + 사용자 공고 HWPX 업로드·관리. 각 템플릿의 폰트/페이지 설정 상세 표시 |
| **미리보기** | 생성된 HWPX 를 HTML 로 렌더 (QTextBrowser) |
| **설정** | API Key 관리, Gemini 모델/일일 한도, 애매 기준 길이, 로그·템플릿·앱데이터 폴더 열기 |

### 고급 팁

- **비용 줄이기**: 설정 탭 "애매 기준 길이" 50 → 80 으로 올리면 Gemini 호출 토큰이 반으로 줍니다 (원고 특성에 따라 ₩5 → ₩3 수준).
- **공고 양식 업로드**: 템플릿 관리 탭에서 기관별 공고 HWPX 를 등록하면, 해당 양식의 폰트/여백/스타일이 그대로 반영됩니다.
- **CLI 배치 처리**: GUI 없이 여러 원고를 한 번에 돌리려면 CLI 사용:
  ```powershell
  python -m src.cli build --template tpl.hwpx --txt input.txt --output out.hwpx --use-gemini --verify
  ```

---

## 자세한 문서

- [USAGE.md](docs/USAGE.md) — GUI/CLI 전체 레퍼런스
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 내부 구조 (개발자용)
- [기획안_v1.md](기획안_v1.md) — 설계 원본
- [CHANGELOG.md](CHANGELOG.md) — 버전 히스토리

---

## 요구 사항

- Windows 10 이상
- **개발 모드**: Python 3.11+
- **배포 모드**: 빌드된 `dist/HwpxAutomation/` 폴더 — 추가 설치 불필요
- Gemini API Key (무료 티어 충분, [aistudio.google.com/apikey](https://aistudio.google.com/apikey))

---

## 개발자용

```powershell
# 소스에서 실행
python -m src.main

# CLI
python -m src.cli build --template <T.hwpx> --txt <I.txt> --output <O.hwpx>

# 테스트
python -m pytest tests/

# 빌드
pyinstaller build.spec --clean --noconfirm
# 결과: dist/HwpxAutomation/
```

85 테스트 통과 (엔진 구조 + GUI smoke + Gemini 스키마 mock). 구조적 검증 7/7.

자세한 구조는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 참고.

---

## 라이선스

**MIT** (이 저장소 자체). 포함된 v1 엔진(Tankongj/hwpx-proposal-automation) 도 원본 MIT 유지.

런타임 의존성:
- PySide6 — LGPL v3 (동적 링크로 준수)
- lxml — BSD
- google-genai — Apache 2.0
- cryptography — Apache 2.0 / BSD
- keyring — MIT

자세한 내용은 [LICENSE](LICENSE).

---

## 로드맵 (v0.3.0+)

- 정량제안서 모드 (서식 채우기)
- 제출서류 체크리스트 (RFP PDF → LLM 추출 + OCR)
- OpenAI / Anthropic 백엔드 추가 (ResolverClient 위에 얹기)
- Self-MoA (단일 모델 N회 + aggregator)
- 모델 목록 런타임 조회 / 원격 가격표
- 상업화 (회원제 + 광고)
