; HWPX Automation — Inno Setup 스크립트 (v0.17.5)
;
; 빌드: iscc.exe /DVersion=0.17.5 installer/setup.iss
; 산출: installer/output/HwpxAutomation-Setup-v0.17.5.exe
;
; 디자인 원칙:
; - 한국어 UI (공무원/행정사/법무사 타깃)
; - Program Files 가 아닌 %LOCALAPPDATA% 에 설치 (관리자 권한 불필요, UAC 경고 없음)
; - 바탕화면 + 시작메뉴 바로가기 (기본 체크)
; - 제어판 "프로그램 추가/제거" 등록
; - 자동 업데이트 (Firebase manifest 기반) 는 앱 자체가 담당 — installer 는 설치만

#ifndef Version
  #define Version "0.17.5"
#endif

#define AppName "HWPX Automation"
#define AppPublisher "장영진"
#define AppURL "https://github.com/Tankongj/hwpx-automation"
#define AppExeName "HwpxAutomation.exe"
#define AppId "{{A1B2C3D4-E5F6-4789-A0B1-C2D3E4F5A6B7}"  ; 고정 GUID — 업데이트 시 동일 유지

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#Version}
AppVerName={#AppName} v{#Version}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; 관리자 권한 불필요 — %LOCALAPPDATA% 에 설치 (UAC 경고 회피)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DefaultDirName={autopf}\HwpxAutomation
DefaultGroupName=HWPX Automation
DisableProgramGroupPage=yes
DisableDirPage=no

; MIT 라이선스 표시
LicenseFile=..\LICENSE

; 빌드 설정
OutputDir=output
OutputBaseFilename=HwpxAutomation-Setup-v{#Version}
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4

; 64-bit 앱
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

; UI
WizardStyle=modern
WizardSizePercent=120
SetupMutex=HwpxAutomationSetupMutex{#AppId}

; 다운로드 크기 + 설치 크기 표시
AppCopyright=© 2026 {#AppPublisher}
VersionInfoVersion={#Version}
VersionInfoProductVersion={#Version}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=HWPX Automation 설치 프로그램
VersionInfoProductName={#AppName}

; 제거 시 사용자 설정 보존 (config.json, user_db 등은 %APPDATA% 에 있으므로 자동 보존)
UninstallDisplayName={#AppName} v{#Version}
UninstallDisplayIcon={app}\{#AppExeName}

; 언어: 한국어 + 영어 (영어는 fallback)
ShowLanguageDialog=auto

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 바탕화면 바로가기 (기본 체크)
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
; 빠른 실행 바로가기 (기본 비체크)
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 6.1

[Files]
; PyInstaller 출력 폴더 통째로 복사
Source: "..\dist\HwpxAutomation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; (선택) 설치 후 README 한 부 복사
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion

[Icons]
; 시작 메뉴
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:ProgramOnTheWeb,{#AppName}}"; Filename: "{#AppURL}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
; 바탕화면
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
; 빠른 실행 (Win 7 이하)
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: quicklaunchicon

[Run]
; 설치 완료 후 실행 옵션
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 제거 시 앱 폴더의 로그/캐시만 삭제 (사용자 설정 %APPDATA% 는 건드리지 않음)
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
function RemoveQuotes(S: String): String;
begin
  if (Length(S) >= 2) and (S[1] = '"') and (S[Length(S)] = '"') then
    Result := Copy(S, 2, Length(S) - 2)
  else
    Result := S;
end;

// 기존 버전 감지 시 자동 제거 (수동 재설치 시나리오)
// 자동 업데이트는 앱 내부 메커니즘이 담당 — 이건 수동 재설치 대응용
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
  UninstallString: String;
  RegKey: String;
begin
  Result := True;
  RegKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + '{#AppId}' + '_is1';
  if RegQueryStringValue(HKCU, RegKey, 'UninstallString', UninstallString) or
     RegQueryStringValue(HKLM, RegKey, 'UninstallString', UninstallString) then
  begin
    if MsgBox('기존 ' + '{#AppName}' + ' 이(가) 설치되어 있습니다.' + #13#10 +
             '먼저 제거 후 계속하시겠습니까?' + #13#10 +
             '(사용자 설정은 보존됩니다)', mbConfirmation, MB_YESNO) = IDYES then
    begin
      UninstallString := RemoveQuotes(UninstallString);
      Exec(UninstallString, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE,
           ewWaitUntilTerminated, ResultCode);
      Sleep(1000);
    end
    else
    begin
      Result := False;
    end;
  end;
end;
