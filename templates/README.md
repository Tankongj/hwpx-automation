# 템플릿 디렉토리

이 폴더는 **번들 기본 템플릿**을 담습니다. 설치 시 (또는 최초 실행 시) `%APPDATA%/HwpxAutomation/templates/` 로 복사되고, 이후에는 `template_manager`가 그 사본을 관리합니다.

MVP 단계에서는 다음 1개 파일이 필수입니다:

- `00_기본_10단계스타일.hwpx` — 휴먼명조/HY견고딕, 10단계 스타일(Ctrl+1~0), A4 여백 스펙. 실제 파일은 기존 귀농귀촌 아카데미 v1 프로젝트의 `templates/정성제안서 서식.hwpx` 를 복사해 쓸 예정.

개발 중에는 `src.cli` 로 임의의 HWPX 템플릿을 `--template` 인자로 넘길 수 있습니다.
