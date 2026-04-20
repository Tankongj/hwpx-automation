# 빌드된 .exe 설치 경험 테스트 가이드

## 자동 smoke 테스트

빌드 직후 다음 명령으로 기본 검증:

```powershell
# dist 폴더가 생성됐는지
Test-Path dist/HwpxAutomation/HwpxAutomation.exe

# 크기 확인 (보통 150~300 MB)
Get-Item dist/HwpxAutomation/HwpxAutomation.exe | Select-Object Length

# 복사해서 독립 실행 (다른 경로에서 동작 확인)
$test = "$env:TEMP\HwpxAutomationTest"
Remove-Item -Recurse -Force $test -ErrorAction SilentlyContinue
Copy-Item -Recurse dist/HwpxAutomation $test
& $test/HwpxAutomation.exe
```

## 수동 확인 체크리스트

앱을 실행해서 다음이 동작하는지 확인:

### 첫 실행 온보딩 (API Key 없는 깨끗한 환경이 있어야 유효)
- [ ] 창이 뜸
- [ ] API Key 입력 다이얼로그 자동 표시
- [ ] 키 입력 → "연결 테스트" → "저장" 누르면 닫힘
- [ ] Windows 자격 증명 관리자에 `HwpxAutomation` 항목 생성

### 변환 탭
- [ ] 템플릿 드롭다운에 "기본 10단계 스타일" 있음
- [ ] 원고 .txt 파일 선택 가능
- [ ] "변환 실행" 클릭 → 진행 로그 실시간 표시
- [ ] 약 5~10초 후 "✅ 변환 완료" 출력
- [ ] "미리보기 탭으로" 버튼 활성화 → 클릭 시 미리보기 탭 이동

### 템플릿 관리 탭
- [ ] 템플릿 목록 표시
- [ ] 별도 HWPX 파일 업로드 → 이름 지정 → 목록 추가
- [ ] 상세 창에 폰트/페이지 설정 표시
- [ ] 기본으로 설정 / 삭제 버튼 동작

### 미리보기 탭
- [ ] HTML 로 렌더된 HWPX 문서 표시
- [ ] "한/글로 열기" 버튼 → 한/글 실행 (설치돼 있으면)

### 설정 탭
- [ ] API Key 상태 "✅ 등록됨"
- [ ] "연결 테스트" → "사용 가능 모델 N개" 알림
- [ ] "로그 폴더 열기" → `%APPDATA%\HwpxAutomation\logs` 탐색기
- [ ] "템플릿 폴더 열기" → `%APPDATA%\HwpxAutomation\templates`
- [ ] "애매 기준 길이" 80 으로 변경 → 저장 → 재시작 후 값 유지

### 에러 핸들링
- [ ] 잘못된 템플릿(깨진 zip) 업로드 → 친절한 에러 메시지
- [ ] 변환 실패 시 "로그 저장하시겠습니까?" 다이얼로그

## 성공 기준

**MVP DoD (기획안 6장)**:
- 기본 템플릿으로 4만자 원고 변환 성공
- 사용자 공고 HWPX 업로드 → 등록 → 변환 성공
- 4 UI 요소 모두 동작
- 첫 실행 온보딩 + 재실행 자동 로드
- `.exe` 1개(폴더 기반)로 다른 PC 에서 설치 없이 실행 가능
