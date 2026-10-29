# DART-OT 인계 기록

- 목적: DART 수집 오류 구분, 원문 근거 보존, 오류 복귀 개선.
- UI·UX 적용 여부: 적용 / 화면 있음.
- 상태: v0.1.1 구현·로컬 검증 완료, GitHub 배포 준비. 시작 시 작업 트리 깨끗함.
- 결정: 기존 화면·판정 기준 유지, 공통 API 계층과 원문추적 시트 추가. 별도 정답 대조 없이 정확도 개선율을 주장하지 않음.
- 관련 파일: app.py, disclosure_viewer.py, dart_api.py, tests/test_dart_reliability.py, scripts/ui_smoke.py, docs/spec.md, docs/design.md, docs/plan.md, docs/todo.md.
- 실행: `_start.bat` 또는 소스용 DART-OT.exe. 배포는 기존 Windows Actions와 새 v0.1.1 태그 사용 예정.
- 완료: 공통 요청 계층, XML/JSON 오류 검사, 오류 캐시 방지, 3개 근거 시트, GUI 지연 콜백, 오류 복귀, 결과 표 스크롤, 하단 버튼 공간 확보.
- 실제 검증 결과 (2026-10-29): unittest 18개 통과. 실제 DART 회사 목록 119,816건(삼성전자 정확 일치 1건) 확인. 삼성전자 2026.03 보고서 1건에서 123개 추출 행과 매출 기준을 수집하고 `outputs/validation_v0_1_1.xlsx` 생성. API 키는 출력·산출물에 기록하지 않음.
- 화면 검증: Computer Use로 입력 화면 확인. 실제 Tk `scripts/ui_smoke.py --automated`에서 오류→입력 보존→재검색→검토→Excel 생성→최소 1180×760 창 버튼 노출 확인. 검증 실행은 설정 저장을 막고 모의 키·모의 데이터를 사용한다.
- 발견·수정: 최소 창에서 표가 하단 버튼을 밀어내던 실패를 재현하여 footer 공간을 먼저 확보. 결과 화면의 API 입력 패널을 접고 작은 브랜드 표시로 대체하여 표도 확보. 동일 검증 재실행 통과. 회사 목록 XML도 result 태그를 쓰지만 status 없이 list를 담는 정상 응답이므로 오류 봉투와 구분하는 테스트 추가. Windows 캡처 도구의 승인 시간 초과·다른 전경 창 캡처는 대상 창 활성화 후 재시도로 해결.
- 미확인: GitHub Windows 패키지 빌드·첨부, 배포 EXE 실기동, 전 기업 원문 정답 대조. 이번 작업으로 정확도 상승률은 산출하지 않았다.
- 실패한 접근: 제한된 쉘의 외부 연결은 차단되어 승인된 외부 연결로 재실행했다. 로컬 Python에 PyInstaller가 없으므로 기존 GitHub Windows 빌드 경로를 사용한다.
- 다음 작업: 변경 리뷰 후 커밋·v0.1.1 태그 게시, Actions와 Windows ZIP·SHA256 확인.
