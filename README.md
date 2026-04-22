# HWPX Automation

> 한글(HWPX) 문서를 AI 가 알아서 만들어주는 **Windows 데스크톱 앱**
>
> 공무원·행정사·법무사·변호사 등 한글 문서를 반복 작성하는 분들을 위해 만들었습니다.

---

## 📥 설치 방법 (Windows — 3분)

### 1️⃣ 최신 설치 파일 다운로드

👉 **[여기 클릭 → 최신 설치 파일 받기](https://github.com/Tankongj/hwpx-automation/releases/latest)**

페이지 아래쪽 **"Assets"** 섹션에서 **`HwpxAutomation-Setup-v0.xx.x.exe`** 파일을 클릭해 받으세요.

| 파일 | 설명 | 누가 |
|------|------|------|
| **`HwpxAutomation-Setup-v0.xx.x.exe`** ⭐ | 설치 프로그램 (추천) | **일반 사용자** |
| `full.zip` | 압축 파일 | 개발자/고급 사용자 |
| `manifest.json` | 자동 업데이트용 | 앱 내부 사용 |

### 2️⃣ 다운로드한 `.exe` 파일 더블클릭

설치 마법사가 한국어로 안내해드립니다:
1. **"다음"** 2~3번 클릭
2. 설치 위치 확인 (기본값 그대로 OK)
3. **"바탕화면 바로가기 만들기"** 체크돼 있는지 확인
4. **"설치"** 클릭 → 1~2분 대기
5. **"마침"** 클릭

### 3️⃣ ⚠️ "Windows에서 PC를 보호했습니다" 경고가 뜨면

이건 **서명 안 된 신규 앱에 대한 Windows 의 일반적 경고**입니다. 바이러스 아니니 안심하세요.

**대처 방법** (2번 클릭):

```
┌──────────────────────────────────────┐
│ Windows에서 PC를 보호했습니다       │
│                                      │
│ [추가 정보]  ← 이거 클릭            │
│                                      │
└──────────────────────────────────────┘

         ↓ 클릭 후

┌──────────────────────────────────────┐
│ 게시자: 알 수 없음                  │
│ 앱:     HwpxAutomation-Setup-xxx.exe│
│                                      │
│ [실행]   [실행 안 함]               │
│  ↑ 이거 클릭                        │
└──────────────────────────────────────┘
```

> 💡 **왜 경고가 뜨나요?** Microsoft 코드 서명 인증서는 연간 수십만원이라 초기 배포 단계에서는 생략했습니다. 사용자 누적되면 서명 추가 예정입니다.

### 4️⃣ 바탕화면 아이콘 더블클릭으로 실행

끝입니다. 다음부턴 바탕화면 아이콘만 누르면 됩니다.

---

## 🔄 자동 업데이트

설치 후에는 **재설치 없이** 새 버전이 자동으로 설치됩니다:

1. 앱 실행 시 자동으로 새 버전 확인
2. 새 버전 발견 시 **"업데이트 받으시겠습니까?"** 다이얼로그 표시
3. **"예"** 누르면 앱이 알아서 다운로드 → 재시작
4. 사용자 설정 (API 키, 템플릿 등) 은 그대로 보존됨

---

## 📚 주요 기능

### AI 문서 자동화
- **기획안 원고 → 스타일 적용된 HWPX 완성본** (Gemini 2.5 Flash)
- 10단계 계층 스타일 자동 매핑 (제목1~10)
- A4 페이지 여백 표준
- 오탈자 자동 교정

### 제안서 작성 지원
- **입찰공고문/제안요청서 체크리스트 자동 생성**
- 정성·정량 평가표 자동 작성 보조
- PDF/HWP/HWPX 입력 모두 지원

### 고급 기능
- Self-MoA (다중 AI 생성 + 통합) — 정확도 +3~7%
- Gemini Batch API (50% 할인) — 긴 작업용
- Ollama/OpenAI/Anthropic 등 다른 AI 백엔드 선택 가능
- MCP 서버 (Claude Desktop 등과 연동)

### 보안
- API 키는 Windows 자격증명 관리자 (Keychain) 에 안전 보관
- 모든 처리 **로컬 실행** (Gemini 호출 외엔 외부 전송 없음)
- AI 공시 자동 (AI 기본법 준수)

---

## 🎯 사용 대상

- **공무원**: 공문서/보고서/기획안 반복 작성
- **행정사**: 민원·인허가 서류 자동화
- **법무사**: 등기·소송서류 템플릿
- **변호사**: 준비서면·의견서 보조
- 기타 한글 문서 반복 업무가 있는 모든 사무직

---

## ❓ 자주 묻는 질문

### Q. 무료인가요?
네, **무료입니다**. 대신 앱 내에 쿠팡 파트너스 광고가 표시됩니다 (차단 가능).

### Q. API 키가 필요한가요?
Gemini API 키가 필요합니다. **Google AI Studio** 에서 무료로 받을 수 있습니다:
- https://aistudio.google.com/apikey
- 월 1,500회 무료 (개인 사용엔 충분)

### Q. 데이터가 외부로 전송되나요?
AI 처리 (Gemini 호출) 시에만 전송됩니다. 문서 내용 자체는 로컬 저장되고 외부 서버에 저장되지 않습니다.

### Q. Mac 에서도 쓸 수 있나요?
현재는 Windows 만 지원합니다 (HWPX 파일이 주로 한국 Windows 환경에서 사용).

### Q. 회사 PC 에 설치해도 되나요?
네. 관리자 권한 없이 현재 사용자 계정에만 설치됩니다 (`%LOCALAPPDATA%\Programs`).

### Q. 제거 방법은?
**설정 → 앱 → 설치된 앱 → HWPX Automation → 제거**. 사용자 설정은 제거 시 그대로 보존됩니다.

### Q. 업데이트가 안 돼요.
앱 재시작 후 **메뉴 → 업데이트 확인** 또는 위 "최신 설치 파일" 링크에서 수동 다운로드 후 재설치해도 됩니다 (사용자 설정은 보존).

---

## 🛠 개발자용

### 소스에서 실행

```powershell
git clone https://github.com/Tankongj/hwpx-automation.git
cd hwpx-automation
pip install -e ".[dev,build]"
python -m src.main
```

### 빌드

```powershell
pyinstaller build.spec --noconfirm
# 결과: dist/HwpxAutomation/HwpxAutomation.exe
```

### 테스트

```powershell
pytest tests/ -q
# 488 passed, 8 skipped (optional deps 자동 skip)
```

### 릴리스 (관리자만)

```powershell
# main 에 변경사항 push 후
git tag v0.x.x
git push origin v0.x.x
# → GitHub Actions 가 자동으로 빌드 + installer + Firebase 배포
```

---

## 📜 라이선스

**MIT License** — 자유롭게 사용·수정·배포 가능. 자세한 내용은 [LICENSE](LICENSE) 참조.

## 🔗 관련 링크

- [GitHub Releases](https://github.com/Tankongj/hwpx-automation/releases) — 모든 버전 다운로드
- [CHANGELOG](CHANGELOG.md) — 버전별 변경사항
- [Issues](https://github.com/Tankongj/hwpx-automation/issues) — 버그 신고 / 기능 요청
- [자동 업데이트 Manifest](https://hwpx-automation.web.app/api/manifest.json) — 현재 최신 버전 정보

---

<div align="center">

**🇰🇷 Made in Korea · 한국 공공·법무 시장을 위해 설계**

문의: [GitHub Issues](https://github.com/Tankongj/hwpx-automation/issues)

</div>
